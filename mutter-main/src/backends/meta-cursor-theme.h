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

#include "meta/meta-backend.h"

#define META_TYPE_CURSOR_THEME meta_cursor_theme_get_type ()
G_DECLARE_FINAL_TYPE (MetaCursorTheme, meta_cursor_theme,
                      META, CURSOR_THEME, GObject)

MetaCursorTheme * meta_cursor_theme_new (MetaBackend *backend);

ClutterCursor * meta_cursor_theme_get_cursor (MetaCursorTheme   *cursor_theme,
                                              ClutterCursorType  cursor_type);

void meta_cursor_theme_reset (MetaCursorTheme *cursor_theme);
