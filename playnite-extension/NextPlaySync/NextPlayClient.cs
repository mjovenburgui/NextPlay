// Lo que manda la extensión a NextPlay y cómo: los datos de cada juego (GameEntry), el envío entero (SyncPayload) y la
// petición a la dirección api/playnite/ con el código personal (NextPlayClient). No depende de Playnite, para poder
// probarlo desde fuera con playnite-extension/Prueba. Lo recibe accounts.playnite, en NextPlay.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;

namespace NextPlaySync
{
    // Un juego tal como lo espera NextPlay: los campos se llaman como allí.
    public class GameEntry
    {
        public string id;
        public string name;
        public string source;
        public List<string> platforms = new List<string>();
        // Tiempo jugado en segundos, como lo cuenta Playnite.
        public ulong playtime;
        // Última partida en ISO 8601 y en UTC (IsoUtc), o null si nunca se ha jugado.
        public string last_activity;
        public string completion_status;
        // Nota de 0 a 100, o null si no tiene.
        public int? user_score;
        public bool hidden;

        // La fecha date como la espera NextPlay: ISO 8601 en UTC y sin fracciones de segundo, o null.
        public static string IsoUtc(DateTime? date) =>
            date?.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
    }

    // Un envío: la biblioteca entera («biblioteca») o el juego que se acaba de cerrar («partida»).
    public class SyncPayload
    {
        public int version = 1;
        public string kind;
        public List<GameEntry> games = new List<GameEntry>();
    }

    // Lo que contesta NextPlay: si ha ido bien, el texto para enseñarlo en Playnite y las cifras.
    public class SyncResult
    {
        public bool Ok;
        public string Message;
        public int Added;
        public int Updated;
        public int Unmatched;
        public int Skipped;
    }

    public static class NextPlayClient
    {
        // La dirección que recibe los envíos, detrás de la de NextPlay.
        public const string ApiPath = "/api/playnite/";
        // La primera vez NextPlay busca en IGDB los juegos que no conoce, y puede tardar.
        private static readonly HttpClient http = new HttpClient { Timeout = TimeSpan.FromMinutes(3) };

        static NextPlayClient()
        {
            // .NET Framework 4.6.2 no activa TLS 1.2 por defecto, y lo pide cualquier NextPlay publicado con https.
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
        }

        // El envío en JSON.
        public static string ToJson(SyncPayload payload) =>
            new JavaScriptSerializer { MaxJsonLength = int.MaxValue }.Serialize(payload);

        // Manda payload a NextPlay (serverUrl) con el código token y devuelve lo que contesta. Si no hay conexión o
        // NextPlay dice que no, Ok es false y Message dice por qué.
        public static async Task<SyncResult> SendAsync(string serverUrl, string token, SyncPayload payload)
        {
            var url = (serverUrl ?? "").Trim().TrimEnd('/') + ApiPath;
            using (var request = new HttpRequestMessage(HttpMethod.Post, url))
            {
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", (token ?? "").Trim());
                request.Content = new StringContent(ToJson(payload), Encoding.UTF8, "application/json");
                try
                {
                    using (var response = await http.SendAsync(request).ConfigureAwait(false))
                    {
                        var body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                        return Read(body, response.StatusCode);
                    }
                }
                catch (Exception exc) when (exc is HttpRequestException || exc is TaskCanceledException)
                {
                    return new SyncResult { Ok = false, Message = $"no se ha podido conectar con {url} ({exc.Message})." };
                }
            }
        }

        // Lee lo que contesta NextPlay: {"ok": true, "message", "added", "updated", "unmatched", "skipped"} o
        // {"ok": false, "error"}.
        private static SyncResult Read(string body, HttpStatusCode status)
        {
            Dictionary<string, object> data;
            try
            {
                data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body);
            }
            catch (Exception exc) when (exc is ArgumentException || exc is InvalidOperationException)
            {
                data = null;
            }
            if (data == null)
                return new SyncResult { Ok = false, Message = $"NextPlay ha contestado algo inesperado ({(int)status})." };

            // Una cifra de la respuesta, o 0 si no viene.
            int Count(string key) => data.TryGetValue(key, out var value) && value is int number ? number : 0;
            // Un texto de la respuesta, o null si no viene.
            string Text(string key) => data.TryGetValue(key, out var value) ? value as string : null;

            var ok = data.TryGetValue("ok", out var flag) && flag is bool yes && yes;
            return new SyncResult
            {
                Ok = ok,
                Message = ok ? Text("message") : (Text("error") ?? $"NextPlay ha dicho que no ({(int)status})."),
                Added = Count("added"),
                Updated = Count("updated"),
                Unmatched = Count("unmatched"),
                Skipped = Count("skipped"),
            };
        }
    }
}
