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

#include "wayland/meta-wayland-background-effect.h"

#include <glib.h>
#include <wayland-server.h>

#include "ext-background-effect-v1-server-protocol.h"
#include "wayland/meta-wayland-private.h"
#include "wayland/meta-wayland-region.h"
#include "wayland/meta-wayland-surface-private.h"
#include "wayland/meta-wayland-versions.h"

#define BACKGROUND_EFFECT_SURFACE_DATA "-meta-wayland-background-effect-surface"

typedef struct _MetaWaylandBackgroundEffectSurface
{
  MetaWaylandSurface *surface;
  gulong destroy_handler_id;
} MetaWaylandBackgroundEffectSurface;

static void
background_effect_surface_unset_pending_blur_region (MetaWaylandSurface *surface)
{
  MetaWaylandSurfaceState *pending;

  if (!surface)
    return;

  pending = meta_wayland_surface_get_pending_state (surface);
  if (!pending)
    return;

  g_clear_pointer (&pending->background_blur_region, mtk_region_unref);
  pending->background_blur_region_set = TRUE;
}

static void
background_effect_surface_destructor (struct wl_resource *resource)
{
  MetaWaylandBackgroundEffectSurface *background_effect_surface =
    wl_resource_get_user_data (resource);

  if (background_effect_surface->surface)
    {
      background_effect_surface_unset_pending_blur_region (
        background_effect_surface->surface);

      g_object_set_data (G_OBJECT (background_effect_surface->surface),
                         BACKGROUND_EFFECT_SURFACE_DATA,
                         NULL);
      g_clear_signal_handler (&background_effect_surface->destroy_handler_id,
                              background_effect_surface->surface);
    }

  g_free (background_effect_surface);
}

static void
background_effect_surface_destroy (struct wl_client   *client,
                                   struct wl_resource *resource)
{
  wl_resource_destroy (resource);
}

static void
background_effect_surface_set_blur_region (struct wl_client   *client,
                                           struct wl_resource *resource,
                                           struct wl_resource *region_resource)
{
  MetaWaylandBackgroundEffectSurface *background_effect_surface =
    wl_resource_get_user_data (resource);
  MetaWaylandSurface *surface = background_effect_surface->surface;
  MetaWaylandSurfaceState *pending;

  if (!surface)
    {
      wl_resource_post_error (resource,
                              EXT_BACKGROUND_EFFECT_SURFACE_V1_ERROR_SURFACE_DESTROYED,
                              "Surface destroyed");
      return;
    }

  pending = meta_wayland_surface_get_pending_state (surface);

  g_clear_pointer (&pending->background_blur_region, mtk_region_unref);
  if (region_resource)
    {
      MetaWaylandRegion *region = wl_resource_get_user_data (region_resource);
      MtkRegion *mtk_region = meta_wayland_region_peek_region (region);

      pending->background_blur_region = mtk_region_copy (mtk_region);
    }

  pending->background_blur_region_set = TRUE;
}

static const struct ext_background_effect_surface_v1_interface
  background_effect_surface_implementation =
{
  background_effect_surface_destroy,
  background_effect_surface_set_blur_region,
};

static void
background_effect_manager_destroy (struct wl_client   *client,
                                   struct wl_resource *resource)
{
  wl_resource_destroy (resource);
}

static void
on_surface_destroyed (MetaWaylandSurface                 *surface,
                      MetaWaylandBackgroundEffectSurface *background_effect_surface)
{
  background_effect_surface->surface = NULL;
}

static void
background_effect_manager_get_background_effect (struct wl_client   *client,
                                                 struct wl_resource *resource,
                                                 uint32_t            id,
                                                 struct wl_resource *surface_resource)
{
  MetaWaylandSurface *surface = wl_resource_get_user_data (surface_resource);
  MetaWaylandBackgroundEffectSurface *background_effect_surface;
  struct wl_resource *background_effect_surface_resource;

  background_effect_surface =
    g_object_get_data (G_OBJECT (surface), BACKGROUND_EFFECT_SURFACE_DATA);
  if (background_effect_surface)
    {
      wl_resource_post_error (resource,
                              EXT_BACKGROUND_EFFECT_MANAGER_V1_ERROR_BACKGROUND_EFFECT_EXISTS,
                              "Background effect resource already exists on surface");
      return;
    }

  background_effect_surface_resource =
    wl_resource_create (client,
                        &ext_background_effect_surface_v1_interface,
                        wl_resource_get_version (resource),
                        id);

  background_effect_surface = g_new0 (MetaWaylandBackgroundEffectSurface, 1);
  background_effect_surface->surface = surface;
  background_effect_surface->destroy_handler_id =
    g_signal_connect (surface,
                      "destroy",
                      G_CALLBACK (on_surface_destroyed),
                      background_effect_surface);

  g_object_set_data (G_OBJECT (surface),
                     BACKGROUND_EFFECT_SURFACE_DATA,
                     background_effect_surface);

  wl_resource_set_implementation (background_effect_surface_resource,
                                  &background_effect_surface_implementation,
                                  background_effect_surface,
                                  background_effect_surface_destructor);
}

static const struct ext_background_effect_manager_v1_interface
  background_effect_manager_implementation =
{
  background_effect_manager_destroy,
  background_effect_manager_get_background_effect,
};

static void
background_effect_manager_bind (struct wl_client *client,
                                void             *data,
                                uint32_t          version,
                                uint32_t          id)
{
  struct wl_resource *resource;

  resource = wl_resource_create (client,
                                 &ext_background_effect_manager_v1_interface,
                                 version,
                                 id);
  wl_resource_set_implementation (resource,
                                  &background_effect_manager_implementation,
                                  NULL,
                                  NULL);

  ext_background_effect_manager_v1_send_capabilities (
    resource,
    EXT_BACKGROUND_EFFECT_MANAGER_V1_CAPABILITY_BLUR);
}

void
meta_wayland_init_background_effect (MetaWaylandCompositor *compositor)
{
  if (!wl_global_create (compositor->wayland_display,
                         &ext_background_effect_manager_v1_interface,
                         META_EXT_BACKGROUND_EFFECT_V1_VERSION,
                         compositor,
                         background_effect_manager_bind))
    g_warning ("Failed to create ext_background_effect_manager_v1 global");
}
