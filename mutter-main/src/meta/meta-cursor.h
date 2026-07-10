/*
 * Copyright 2026 Red Hat, Inc.
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License as
 * published by the Free Software Foundation; either version 2 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, see <http://www.gnu.org/licenses/>.
 *
 * Author: Carlos Garnacho <carlosg@gnome.org>
 */

#pragma once

#include <glib-object.h>

#include "clutter/clutter.h"
#include "meta/meta-backend.h"

#define META_CURSOR_EXTENSION_POINT_NAME "meta-cursor"

struct _MetaCursorClass
{
  ClutterCursorClass parent_class;
};

#define META_TYPE_CURSOR meta_cursor_get_type ()

META_EXPORT
G_DECLARE_DERIVABLE_TYPE (MetaCursor, meta_cursor, META, CURSOR, ClutterCursor)

META_EXPORT
MetaBackend * meta_cursor_get_backend (MetaCursor *cursor);

META_EXPORT
const char * meta_cursor_get_theme_name (MetaCursor *cursor);

META_EXPORT
unsigned int meta_cursor_get_size (MetaCursor *cursor);
