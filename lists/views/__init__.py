"""Vistas de las listas, un archivo por pantalla:

- pagina_de_la_lista.py: Página de la lista (y sus «me gusta», comentarios, denuncias y «Lo quiero» de lo que te falta).
- editar_lista.py: Editar lista (sus datos y sus juegos: buscar, añadir, quitar, mover, comentar y poner en un nivel).
- colaboradores.py: quién puede editar una lista (en Editar lista) y aceptar o dejar una invitación (en la Página de
  la lista).
- nueva_lista.py: Nueva lista y Nueva tier list.
- listas_del_juego.py: Listas del juego y las listas que enseña la Ficha del juego.
- tarjetas.py: lo que enseñan de las listas el Perfil y el feed de Inicio.
- comun.py: las listas que puede tocar el usuario (las suyas y en las que colabora).

Aquí se reexportan las vistas y lo que usan otras apps, para que urls.py y quien importa de lists.views no cambien.
"""

from .colaboradores import accept, collaborator_remove, invite, leave
from .editar_lista import add, delete, edit, move, note, remove, search, tier
from .listas_del_juego import game_lists, game_page_context
from .nueva_lista import from_library, new, new_tier_list
from .pagina_de_la_lista import comment, comment_delete, comment_report, detail, like, report, wish_missing
from .tarjetas import SHOWCASE_LISTS, SHOWCASE_TIER_LISTS, mark_boards, mark_liked_lists, mark_progress, profile_lists
