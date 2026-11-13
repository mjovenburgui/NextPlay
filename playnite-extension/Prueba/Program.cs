// Prueba de NextPlay Sync sin Playnite: manda a un NextPlay tres juegos de ejemplo con el mismo código que usa la
// extensión (NextPlayClient) y enseña lo que contesta. Con «partida» manda solo Hades, con una hora más que en la
// biblioteca, como al cerrar una partida.
// Uso: Prueba.exe <dirección de NextPlay> <código> [biblioteca|partida]
using System;
using System.Collections.Generic;
using System.Text;
using NextPlaySync;

internal static class Program
{
    private static int Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;
        if (args.Length < 2)
        {
            Console.WriteLine("Uso: Prueba <dirección de NextPlay> <código> [biblioteca|partida]");
            return 2;
        }
        var kind = args.Length > 2 ? args[2] : "biblioteca";
        var hades = new GameEntry
        {
            id = "00000000-0000-0000-0000-000000000001",
            name = "Hades",
            source = "Epic",
            platforms = { "PC (Windows)" },
            playtime = kind == "partida" ? 39600UL : 36000UL,
            last_activity = GameEntry.IsoUtc(DateTime.Now.AddMinutes(-5)),
            completion_status = "Completed",
            user_score = 90,
        };
        var games = new List<GameEntry> { hades };
        if (kind != "partida")
        {
            games.Add(new GameEntry
            {
                id = "00000000-0000-0000-0000-000000000002",
                name = "Disco Elysium",
                source = "GOG",
                platforms = { "PC (Windows)" },
                playtime = 0,
                completion_status = "Not Played",
            });
            games.Add(new GameEntry
            {
                id = "00000000-0000-0000-0000-000000000003",
                name = "Portal 2",
                source = "Steam",
                playtime = 18000,
                last_activity = GameEntry.IsoUtc(new DateTime(2024, 3, 1, 20, 0, 0)),
            });
        }
        var payload = new SyncPayload { kind = kind, games = games };
        Console.WriteLine(NextPlayClient.ToJson(payload));
        var result = NextPlayClient.SendAsync(args[0], args[1], payload).GetAwaiter().GetResult();
        Console.WriteLine(
            $"{(result.Ok ? "BIEN" : "MAL")}: {result.Message} (añadidos {result.Added}, actualizados {result.Updated}, "
            + $"sin encontrar {result.Unmatched}, saltados {result.Skipped})");
        return result.Ok ? 0 : 1;
    }
}
