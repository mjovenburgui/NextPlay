import logging
import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Max, Q
from django.utils import timezone

from games import igdb

from ... import steam

User = get_user_model()
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Vuelve a importar la biblioteca de Steam (horas y juegos nuevos) de los usuarios con la "
        "sincronización nocturna activada. Pensado para lanzarlo cada noche (comando nightly)."
    )

    def add_arguments(self, parser):
        """Opciones: --pause (segundos entre usuarios), --min-hours (saltar a los recién sincronizados) y --user."""
        parser.add_argument(
            "--pause", type=float, default=3,
            help="Segundos de espera entre usuarios, para repartir las llamadas a Steam e IGDB (3 por defecto).",
        )
        parser.add_argument(
            "--min-hours", type=float, default=12,
            help="Se salta a quien se haya sincronizado hace menos de estas horas, a mano o sola (12 por defecto).",
        )
        parser.add_argument("--user", help="Solo este usuario, aunque tenga la sincronización desactivada o reciente.")

    def handle(self, *args, **options):
        """Recorre los usuarios con cuentas de Steam, del que lleva más tiempo sin sincronizar al que menos.

        Un usuario que falla (perfil privado, Steam o IGDB caídos) no para a los demás: se apunta en el
        log y se sigue. Solo da error al final si no ha podido sincronizar a nadie.
        """
        if not steam.is_configured():
            raise CommandError("Falta STEAM_API_KEY: no se puede leer la biblioteca de Steam.")
        users = (
            User.objects.filter(steam_accounts__isnull=False)
            .select_related("profile")
            .annotate(last_sync=Max("steam_accounts__synced_at"))
            .order_by(F("last_sync").asc(nulls_first=True), "pk")
        )
        if options["user"]:
            users = users.filter(username=options["user"])
            if not users:
                raise CommandError(f"{options['user']} no existe o no tiene cuentas de Steam vinculadas.")
        else:
            cutoff = timezone.now() - timedelta(hours=options["min_hours"])
            # Con Q y no con exclude: exclude sobre la anotación también quitaría a los que nunca se sincronizaron.
            users = users.filter(Q(last_sync__isnull=True) | Q(last_sync__lt=cutoff), profile__auto_sync=True)

        done = failed = 0
        for index, user in enumerate(users):
            if index:
                time.sleep(options["pause"])
            for account in user.steam_accounts.all():
                steam.refresh_account(account)
            try:
                result = steam.import_library(user)
            except steam.SteamPrivateError:
                self.stdout.write(f"{user.username}: ninguna cuenta deja ver sus juegos, se salta.")
                continue
            except (steam.SteamError, igdb.IGDBError):
                logger.exception("Falló la sincronización nocturna de Steam de %s", user.username)
                self.stderr.write(f"{user.username}: falló la sincronización (detalles en el log).")
                failed += 1
                continue
            user.profile.auto_synced_at = timezone.now()
            user.profile.save(update_fields=["auto_synced_at"])
            done += 1
            self.stdout.write(
                f"{user.username}: {result['added']} añadidos, {result['updated']} actualizados, "
                f"{result['unmatched']} sin emparejar."
            )

        self.stdout.write(self.style.SUCCESS(f"Steam: {done} bibliotecas sincronizadas, {failed} con error."))
        if failed and not done:
            raise CommandError("No se ha podido sincronizar ninguna biblioteca.")
