from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from games import prices, xbox
from games.models import Game


class Command(BaseCommand):
    help = "Actualiza los precios por tienda: PC (IsThereAnyDeal) y consola Xbox (tienda de Microsoft)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, help="Solo los N juegos más populares.")
        parser.add_argument("--game", help="Solo el juego con este slug.")

    def handle(self, *args, **options):
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
        except (prices.ITADError, xbox.XboxError) as exc:
            raise CommandError(str(exc)) from exc
