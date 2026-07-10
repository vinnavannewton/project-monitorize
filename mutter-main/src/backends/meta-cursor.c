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

#include "config.h"

#include "meta/meta-cursor.h"

enum
{
  PROP_0,
  PROP_BACKEND,
  PROP_THEME_NAME,
  PROP_SIZE,
  N_PROPS,
};

static GParamSpec *props[N_PROPS] = { 0, };

typedef struct _MetaCursorPrivate MetaCursorPrivate;

struct _MetaCursorPrivate
{
  MetaBackend *backend;
  char *theme_name;
  unsigned int size;
};

G_DEFINE_ABSTRACT_TYPE_WITH_CODE (MetaCursor, meta_cursor, CLUTTER_TYPE_CURSOR,
                                  G_ADD_PRIVATE (MetaCursor)
                                  g_io_extension_point_set_required_type (g_io_extension_point_register (META_CURSOR_EXTENSION_POINT_NAME),
                                                                          g_define_type_id))

static void
meta_cursor_finalize (GObject *object)
{
  MetaCursor *cursor = META_CURSOR (object);
  MetaCursorPrivate *priv = meta_cursor_get_instance_private (cursor);

  g_clear_pointer (&priv->theme_name, g_free);

  G_OBJECT_CLASS (meta_cursor_parent_class)->finalize (object);
}

static void
meta_cursor_set_property (GObject      *object,
                          guint         prop_id,
                          const GValue *value,
                          GParamSpec   *pspec)
{
  MetaCursor *cursor = META_CURSOR (object);
  MetaCursorPrivate *priv = meta_cursor_get_instance_private (cursor);

  switch (prop_id)
    {
    case PROP_BACKEND:
      priv->backend = g_value_get_object (value);
      break;
    case PROP_THEME_NAME:
      priv->theme_name = g_value_dup_string (value);
      break;
    case PROP_SIZE:
      priv->size = g_value_get_uint (value);
      break;
    default:
      G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
      break;
    }
}

static void
meta_cursor_class_init (MetaCursorClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);

  object_class->finalize = meta_cursor_finalize;
  object_class->set_property = meta_cursor_set_property;

  props[PROP_BACKEND] =
    g_param_spec_object ("backend", NULL, NULL,
                         META_TYPE_BACKEND,
                         G_PARAM_WRITABLE |
                         G_PARAM_CONSTRUCT_ONLY |
                         G_PARAM_STATIC_STRINGS);
  props[PROP_THEME_NAME] =
    g_param_spec_string ("theme-name", NULL, NULL,
                         NULL,
                         G_PARAM_WRITABLE |
                         G_PARAM_CONSTRUCT_ONLY |
                         G_PARAM_STATIC_STRINGS);
  props[PROP_SIZE] =
    g_param_spec_uint ("size", NULL, NULL,
                       0, G_MAXUINT, 0,
                       G_PARAM_WRITABLE |
                       G_PARAM_CONSTRUCT_ONLY |
                       G_PARAM_STATIC_STRINGS);

  g_object_class_install_properties (object_class, N_PROPS, props);
}

static void
meta_cursor_init (MetaCursor *cursor)
{
}

/**
 * meta_cursor_get_backend:
 * @cursor: a `MetaCursor`
 *
 * Gets the [class@Meta.Backend] of the cursor
 *
 * Returns: (transfer none): the backend
 **/
MetaBackend *
meta_cursor_get_backend (MetaCursor *cursor)
{
  MetaCursorPrivate *priv = meta_cursor_get_instance_private (cursor);

  return priv->backend;
}

const char *
meta_cursor_get_theme_name (MetaCursor *cursor)
{
  MetaCursorPrivate *priv = meta_cursor_get_instance_private (cursor);

  return priv->theme_name;
}

unsigned int
meta_cursor_get_size (MetaCursor *cursor)
{
  MetaCursorPrivate *priv = meta_cursor_get_instance_private (cursor);

  return priv->size;
}
