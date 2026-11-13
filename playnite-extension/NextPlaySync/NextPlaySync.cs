// Extensión de Playnite «NextPlay Sync»: manda a NextPlay los juegos y horas de la biblioteca de Playnite, sobre todo
// los de las tiendas sin API (Epic, GOG, EA, Ubisoft, Battle.net, Amazon…). La biblioteca entera al abrir Playnite si
// hace un día o más de la última vez, y después cada día mientras siga abierto; el juego que se acaba de cerrar, al
// terminar cada partida; y a mano, desde el menú «NextPlay». NextPlay decide qué importa: lo de Steam, PlayStation y
// Xbox lo salta, porque se importa desde su cuenta.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Controls;
using Playnite.SDK;
using Playnite.SDK.Events;
using Playnite.SDK.Models;
using Playnite.SDK.Plugins;

namespace NextPlaySync
{
    public class NextPlaySync : GenericPlugin
    {
        private static readonly ILogger logger = LogManager.GetLogger();
        // Cada cuánto se manda la biblioteca entera.
        private static readonly TimeSpan Daily = TimeSpan.FromHours(24);
        // Cada cuánto se mira si toca, mientras Playnite está abierto; la primera vez, un minuto después de abrirlo
        // para no frenar el arranque.
        private static readonly TimeSpan CheckEvery = TimeSpan.FromHours(1);
        private static readonly TimeSpan FirstCheck = TimeSpan.FromMinutes(1);
        // Lo que se espera tras cerrar un juego: Playnite apunta el tiempo de la partida justo después de avisar.
        private static readonly TimeSpan AfterGame = TimeSpan.FromSeconds(10);
        // Para no mandar dos cosas a la vez (el temporizador, el menú y el final de una partida).
        private readonly SemaphoreSlim sending = new SemaphoreSlim(1, 1);
        private Timer timer;

        internal SettingsViewModel Settings { get; }

        public override Guid Id { get; } = Guid.Parse("8b4c2f3e-5d1a-4e7b-9c6f-2a3d4e5f6a7b");

        public NextPlaySync(IPlayniteAPI api) : base(api)
        {
            Settings = new SettingsViewModel(this);
            Properties = new GenericPluginProperties { HasSettings = true };
        }

        // Los ajustes que Playnite enseña y guarda.
        public override ISettings GetSettings(bool firstRunSettings) => Settings;

        // La pantalla de los ajustes.
        public override UserControl GetSettingsView(bool firstRunSettings) => new SettingsView();

        // Al abrir Playnite: se empieza a mirar cada CheckEvery si toca mandar la biblioteca.
        public override void OnApplicationStarted(OnApplicationStartedEventArgs args)
        {
            timer = new Timer(OnTimer, null, FirstCheck, CheckEvery);
        }

        // Al cerrar Playnite: se deja de mirar.
        public override void OnApplicationStopped(OnApplicationStoppedEventArgs args)
        {
            timer?.Dispose();
        }

        // Al cerrar un juego: se manda solo ese, con su tiempo ya puesto al día, para que la partida llegue a NextPlay
        // el mismo día en que se ha jugado.
        public override void OnGameStopped(OnGameStoppedEventArgs args)
        {
            var id = args.Game.Id;
            Task.Run(async () =>
            {
                await Task.Delay(AfterGame).ConfigureAwait(false);
                var game = PlayniteApi.Database.Games.Get(id);
                if (game != null)
                    await SendAsync(new[] { game }, "partida", notify: false).ConfigureAwait(false);
            });
        }

        // El menú «NextPlay» del menú principal de Playnite: mandar ya la biblioteca entera, con un aviso al acabar.
        public override IEnumerable<MainMenuItem> GetMainMenuItems(GetMainMenuItemsArgs args)
        {
            yield return new MainMenuItem
            {
                MenuSection = "@NextPlay",
                Description = "Sincronizar ahora",
                Action = _ => Task.Run(() => SyncLibraryAsync(force: true, notify: true)),
            };
        }

        // Lo que hace el temporizador: mandar la biblioteca si toca.
        private void OnTimer(object state)
        {
            Task.Run(() => SyncLibraryAsync(force: false, notify: false));
        }

        // Manda la biblioteca entera si nunca se ha mandado o hace Daily o más; con force, siempre.
        private async Task SyncLibraryAsync(bool force, bool notify)
        {
            var last = Settings.Settings.LastLibrarySync;
            if (!force && last.HasValue && DateTime.Now - last.Value < Daily)
                return;
            await SendAsync(PlayniteApi.Database.Games.ToList(), "biblioteca", notify).ConfigureAwait(false);
        }

        // Manda games a NextPlay como kind («biblioteca» o «partida») y apunta en los ajustes qué ha pasado. Avisa en
        // Playnite si falla y, con notify, también si va bien.
        private async Task SendAsync(IEnumerable<Game> games, string kind, bool notify)
        {
            var settings = Settings.Settings;
            if (string.IsNullOrWhiteSpace(settings.Token))
            {
                if (notify)
                    Notify("Falta tu código de NextPlay: pégalo en los ajustes de NextPlay Sync.", NotificationType.Error);
                return;
            }
            await sending.WaitAsync().ConfigureAwait(false);
            try
            {
                var payload = new SyncPayload { kind = kind, games = games.Select(ToEntry).ToList() };
                var result = await NextPlayClient.SendAsync(settings.ServerUrl, settings.Token, payload).ConfigureAwait(false);
                settings.LastResult = $"{DateTime.Now:g} · {(result.Ok ? "" : "Error: ")}{result.Message}";
                if (result.Ok && kind == "biblioteca")
                    settings.LastLibrarySync = DateTime.Now;
                SavePluginSettings(settings);
                if (!result.Ok)
                {
                    logger.Warn($"NextPlay Sync: {result.Message}");
                    Notify($"NextPlay: {result.Message}", NotificationType.Error);
                }
                else if (notify)
                {
                    Notify($"NextPlay: {result.Message}", NotificationType.Info);
                }
            }
            catch (Exception exc)
            {
                logger.Error(exc, "NextPlay Sync no ha podido mandar la biblioteca.");
                Notify($"NextPlay: no se ha podido sincronizar ({exc.Message}).", NotificationType.Error);
            }
            finally
            {
                sending.Release();
            }
        }

        // Un juego de Playnite como lo espera NextPlay.
        private static GameEntry ToEntry(Game game) => new GameEntry
        {
            id = game.Id.ToString(),
            name = game.Name,
            source = game.Source?.Name ?? "",
            platforms = game.Platforms?.Select(platform => platform.Name).ToList() ?? new List<string>(),
            playtime = game.Playtime,
            last_activity = GameEntry.IsoUtc(game.LastActivity),
            completion_status = game.CompletionStatus?.Name ?? "",
            user_score = game.UserScore,
            hidden = game.Hidden,
        };

        // Un aviso en la campana de Playnite; uno por tipo, que se sustituye en vez de acumularse.
        private void Notify(string text, NotificationType type)
        {
            PlayniteApi.Notifications.Add(new NotificationMessage($"NextPlaySync-{type}", text, type));
        }
    }
}
