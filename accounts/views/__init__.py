"""Vistas de las cuentas, un archivo por pantalla:

- registro.py: Registro.
- perfil.py: Perfil (también lo que pinta la vista previa de Editar perfil).
- editar_perfil.py: Editar perfil (el formulario, su vista previa, las imágenes de un juego para la cabecera y las
  tiendas en las que compras).
- cuentas_vinculadas.py: vincular, importar y desvincular Steam, PlayStation, Xbox, GOG y Epic Games y las cuentas de
  otras plataformas (Editar perfil › Cuentas vinculadas y el modal «Vincular cuentas» del Perfil).
- juegos_importados.py: Juegos importados.
- importar_archivo.py: Importar desde un archivo (el CSV de Playnite o de otro tracker).
- sincronizar_playnite.py: la extensión NextPlay Sync de Playnite (su código y su descarga, en Editar perfil › Cuentas
  vinculadas, y la dirección api/playnite/ a la que manda la biblioteca).
- avisos.py: Avisos.
- ficha_del_juego.py: los botones de las cuentas en la Ficha del juego (favoritos, «Tus logros» y poner de cabecera).
- resumen_anual.py: Resumen del año (y su imagen para compartir).
- comun.py: lo que comparten varias (volver a la página desde la que se envió un formulario).

Aquí se reexportan las vistas y lo que usan otras apps, para que urls.py y quien importa de accounts.views no cambien.
"""

from .avisos import notifications
from .cuentas_vinculadas import (
    epic_sync, epic_unlink, gog_sync, gog_unlink, platform_add, platform_remove, psn_sync, psn_unlink,
    steam_auto_sync, steam_callback, steam_import, steam_link, steam_unlink, steam_wishlist, xbox_callback, xbox_link,
    xbox_unlink,
)
from .editar_perfil import banner_arts, edit_profile, profile_preview, shops_update
from .ficha_del_juego import banner_from_game, favorite_context, favorite_toggle, game_achievements
from .importar_archivo import import_file
from .juegos_importados import imported_title_assign, imported_title_hide, imported_titles
from .perfil import profile
from .registro import signup
from .resumen_anual import year_summary, year_summary_image
from .sincronizar_playnite import playnite_api, playnite_extension, playnite_token, playnite_unlink
