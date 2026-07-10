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

#include "config.h"

#include "compositor/meta-surface-content.h"

#include "compositor/meta-surface-actor.h"

struct _MetaSurfaceContent
{
  GObject parent;

  MetaShapedTexture *texture;
};

static void clutter_content_iface_init (ClutterContentInterface *iface);

G_DEFINE_FINAL_TYPE_WITH_CODE (MetaSurfaceContent,
                               meta_surface_content,
                               G_TYPE_OBJECT,
                               G_IMPLEMENT_INTERFACE (CLUTTER_TYPE_CONTENT,
                                                      clutter_content_iface_init))

static void
meta_surface_content_dispose (GObject *object)
{
  MetaSurfaceContent *content = META_SURFACE_CONTENT (object);

  g_clear_object (&content->texture);

  G_OBJECT_CLASS (meta_surface_content_parent_class)->dispose (object);
}

static gboolean
meta_surface_content_get_preferred_size (ClutterContent *content,
                                         float          *width,
                                         float          *height)
{
  MetaSurfaceContent *surface_content = META_SURFACE_CONTENT (content);

  return clutter_content_get_preferred_size (CLUTTER_CONTENT (surface_content->texture),
                                             width,
                                             height);
}

static void
texture_size_changed (MetaShapedTexture  *texture,
                      MetaSurfaceContent *content)
{
  clutter_content_invalidate_size (CLUTTER_CONTENT (content));
  clutter_content_invalidate (CLUTTER_CONTENT (content));
}

static void
meta_surface_content_paint_content (ClutterContent      *content,
                                    ClutterActor        *actor,
                                    ClutterPaintNode    *root_node,
                                    ClutterPaintContext *paint_context)
{
  MetaSurfaceContent *surface_content = META_SURFACE_CONTENT (content);
  MetaShapedTexture *texture = surface_content->texture;
  MtkRegion *clip_region;
  ClutterActorBox alloc;
  uint8_t opacity;

  g_return_if_fail (META_IS_SURFACE_ACTOR (actor));

  clip_region = meta_shaped_texture_get_clip_region (texture);
  if (clip_region && mtk_region_is_empty (clip_region))
    return;

  if (!meta_shaped_texture_get_texture (texture))
    return;

  opacity = clutter_actor_get_paint_opacity (actor);
  clutter_actor_get_content_box (actor, &alloc);

  meta_surface_actor_paint_background_effects (META_SURFACE_ACTOR (actor),
                                               root_node,
                                               paint_context,
                                               &alloc,
                                               meta_shaped_texture_get_width (texture),
                                               meta_shaped_texture_get_height (texture),
                                               clip_region,
                                               opacity);
  meta_shaped_texture_paint_content (texture,
                                     actor,
                                     root_node,
                                     paint_context);
}

static void
clutter_content_iface_init (ClutterContentInterface *iface)
{
  iface->get_preferred_size = meta_surface_content_get_preferred_size;
  iface->paint_content = meta_surface_content_paint_content;
}

static void
meta_surface_content_class_init (MetaSurfaceContentClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);

  object_class->dispose = meta_surface_content_dispose;
}

static void
meta_surface_content_init (MetaSurfaceContent *content)
{
}

MetaSurfaceContent *
meta_surface_content_new (ClutterContext    *clutter_context,
                          ClutterColorState *color_state)
{
  MetaSurfaceContent *content;

  content = g_object_new (META_TYPE_SURFACE_CONTENT, NULL);
  content->texture = meta_shaped_texture_new (clutter_context, color_state);
  g_signal_connect_object (content->texture, "size-changed",
                           G_CALLBACK (texture_size_changed), content,
                           G_CONNECT_DEFAULT);

  return content;
}

MetaShapedTexture *
meta_surface_content_get_texture (MetaSurfaceContent *content)
{
  g_return_val_if_fail (META_IS_SURFACE_CONTENT (content), NULL);

  return content->texture;
}
