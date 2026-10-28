from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from games import nintendo, prices, xbox
from games.models import Game
from library import wishlist


class Command(BaseCommand):
    help = (
        "Actualiza los precios por tienda: PC (IsThereAnyDeal), consola Xbox (tienda de Microsoft) y Nintendo "
        "Switch (eShop europea), y avisa a quien tenga en su lista de deseos un juego que haya bajado."
    )

    def add_arguments(self, parser):
        """Opciones del comando: --limit (solo los N más populares) y --game (un solo juego por slug)."""
        parser.add_argument("--limit", type=int, help="Solo los N juegos más populares.")
        parser.add_argument("--game", help="Solo el juego con este slug.")

    def handle(self, *args, **options):
        """Actualiza los precios de PC (si hay ITAD_API_KEY), los de Xbox y los de Nintendo de los juegos
        elegidos, y después crea los avisos de la lista de deseos."""
        games = Game.objects.prefetch_related("platforms", "prices")
        if options["game"]:
            games = games.filter(slug=options["game"])
        if options["limit"]:
            games = games[: options["limit"]]
        games = list(games)

        try:
            if prices.is_configured():
                total = prices.update_prices(games)
                self.stdout.write(self.style.SUCCESS(f"PC: {total} juegos con precios."))
            else:
                self.stderr.write("PC: sin ITAD_API_KEY, no se actualizan los precios de PC.")
            total = xbox.update_xbox_prices(games)
            self.stdout.write(self.style.SUCCESS(f"Xbox: {total} juegos con precio."))
            total = nintendo.update_nintendo_prices(games)
            self.stdout.write(self.style.SUCCESS(f"Nintendo: {total} juegos con precio."))
        except (prices.ITADError, xbox.XboxError, nintendo.NintendoError) as exc:
            raise CommandError(str(exc)) from exc
        alerts = wishlist.check_alerts(games)
        self.stdout.write(self.style.SUCCESS(f"Avisos de la lista de deseos: {alerts}."))
