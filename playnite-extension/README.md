# NextPlay Sync, la extensión de Playnite

Manda a NextPlay los juegos y las horas de tu biblioteca de Playnite, sobre todo los de las tiendas que no tienen API: Epic, GOG, EA, Ubisoft, Battle.net, Amazon… Así se actualizan solos, sin volver a subir ningún archivo.

## Cuándo manda qué

- **La biblioteca entera:** al abrir Playnite, si hace un día o más de la última vez, y después cada día mientras siga abierto.
- **Cada partida:** al cerrar un juego, solo ese, con su tiempo ya puesto al día. En NextPlay queda como una sesión de juego de ese día, con su duración real.
- **A mano:** menú principal de Playnite › NextPlay › Sincronizar ahora.

NextPlay decide qué importa: lo de Steam, PlayStation y Xbox lo salta, porque se importa desde su cuenta. Los juegos sin tienda (añadidos a mano o emulados) van a «Otras tiendas». El estado y la nota de Playnite solo se ponen en los juegos que entran nuevos.

## Instalarla

1. En NextPlay, en Editar perfil › Cuentas vinculadas › Playnite, pulsa «Generar el código» y descarga la extensión.
2. Abre `NextPlaySync.pext`: Playnite la instala y pide reiniciarse.
3. En Playnite, en Complementos › Ajustes de extensiones › Genéricas › NextPlay Sync, pega la dirección de NextPlay y tu código.

El código solo sirve para mandar tu biblioteca. NextPlay guarda únicamente su huella, así que si lo pierdes hay que generar otro, y el anterior deja de valer.

## Compilarla

Necesita el .NET SDK. Desde la raíz del proyecto:

```powershell
powershell -ExecutionPolicy Bypass -File playnite-extension/build.ps1
```

Deja `playnite-extension/dist/NextPlaySync.pext`, que es lo que descarga NextPlay.

## Probarla sin Playnite

`Prueba` manda tres juegos de ejemplo con el mismo código que la extensión, y con `partida` manda una partida de Hades:

```powershell
dotnet run --project playnite-extension/Prueba -- http://localhost:8000 <tu código>
dotnet run --project playnite-extension/Prueba -- http://localhost:8000 <tu código> partida
```

## Cómo está hecha

- `NextPlaySync/NextPlaySync.cs`: la extensión (GenericPlugin): cuándo manda, el menú y los avisos.
- `NextPlaySync/NextPlayClient.cs`: qué se manda y la petición a `api/playnite/`. No depende de Playnite.
- `NextPlaySync/Settings.cs` y `SettingsView.cs`: los ajustes y su pantalla, hecha en código para que todo quepa en un `.dll`.
- En NextPlay la recibe `accounts/playnite.py`, que importa cada juego como una fila de un archivo (`accounts/csv_import.py`).
