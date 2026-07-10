/* -*- mode: C; c-file-style: "gnu"; indent-tabs-mode: nil; -*- */

/*
 * Copyright (C) 2026 Kristof Imeri
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
 */

#pragma once

#include "compositor/meta-shaped-texture-private.h"

G_BEGIN_DECLS

#define META_TYPE_SURFACE_CONTENT (meta_surface_content_get_type ())
G_DECLARE_FINAL_TYPE (MetaSurfaceContent,
                      meta_surface_content,
                      META, SURFACE_CONTENT,
                      GObject)

MetaSurfaceContent * meta_surface_content_new (ClutterContext    *clutter_context,
                                               ClutterColorState *color_state);
MetaShapedTexture * meta_surface_content_get_texture (MetaSurfaceContent *content);

G_END_DECLS
