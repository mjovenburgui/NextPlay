"""Comando «manage.py sync_gog» (parte de la tarea de cada noche): vuelve a importar, desde el perfil público, la
biblioteca de GOG de quien la ha vinculado en Editar perfil › Cuentas vinculadas › GOG (accounts.gog).
"""

import logging
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q
from django.utils import timezone

from games import igdb

from ... import gog
from ...models import GOGAccount

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Vuelve a importar la biblioteca de GOG (juegos nuevos, horas y última partida) de los perfiles públicos "
        "vinculados. Pensado para lanzarlo cada noche (comando nightly), antes de actualizar los precios."
    )

    def add_arguments(self, parser):
        """Opciones: --pause (segundos entre perfiles), --min-hours (saltar los recién importados) y --user.

        Pantallas: ninguna (opciones del comando sync_gog).
        """
        parser.add_argument(
            "--pause", type=float, default=3,
            help="Segundos de espera entre perfiles, para repartir las llamadas a GOG e IGDB (3 por defecto).",
        )
        parser.add_argument(
            "--min-hours", type=float, default=12,
            help="Se salta a quien se haya importado hace menos de estas horas, a mano o solo (12 por defecto).",
        )
        parser.add_argument("--user", help="Solo este usuario, aunque se haya importado hace poco.")

    def handle(self, *args, **options):
        """Recorre los perfiles de GOG vinculados, del que lleva más tiempo sin importarse al que menos.

        Uno que falla no para a los demás: si el perfil ya no es público o el usuario de GOG ya no existe, se apunta en
        su cuenta (Editar perfil lo avisa) y se sigue; si falla GOG o IGDB, se apunta en el log. Solo da error al final
        si no ha podido importar ninguno.

        Pantallas: Editar perfil (Cuentas vinculadas › GOG: la última importación o el aviso), Perfil (biblioteca y
        horas), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos (progreso), Página
        del reto (progreso).
        """
        accounts = GOGAccount.objects.select_related("user").order_by(F("synced_at").asc(nulls_first=True), "pk")
        if options["user"]:
            accounts = accounts.filter(user__username=options["user"])
            if not accounts:
                raise CommandError(f"{options['user']} no existe o no tiene GOG vinculado.")
        else:
            cutoff = timezone.now() - timedelta(hours=options["min_hours"])
            accounts = accounts.filter(Q(synced_at__isnull=True) | Q(synced_at__lt=cutoff))

        done = failed = 0
        for index, account in enumerate(accounts):
            if index:
                time.sleep(options["pause"])
            name = account.user.username
            try:
                result = gog.sync(account)
            except gog.GOGPrivateError:
                self.stdout.write(f"{name}: el perfil de GOG {account.username} ya no es público, se salta.")
                continue
            except gog.GOGMissingError:
                self.stdout.write(f"{name}: ya no hay ningún usuario de GOG {account.username}, se salta.")
                continue
            except (gog.GOGError, igdb.IGDBError):
                logger.exception("Falló la sincronización nocturna de GOG de %s", name)
                self.stderr.write(f"{name}: falló la sincronización de GOG (detalles en el log).")
                failed += 1
                continue
            done += 1
            self.stdout.write(
                f"{name}: {result['added']} añadidos, {result['updated']} actualizados, "
                f"{result['unmatched']} sin emparejar."
            )

        self.stdout.write(self.style.SUCCESS(f"GOG: {done} bibliotecas sincronizadas, {failed} con error."))
        if failed and not done:
            raise CommandError("No se ha podido sincronizar ninguna biblioteca de GOG.")
