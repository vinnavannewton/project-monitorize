/*
 * Copyright (C) 2026 Red Hat, Inc.
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

#include "tests/meta-x11-test-utils.h"

#include <gio/gio.h>

#include "core/display-private.h"
#include "wayland/meta-wayland.h"

struct _MetaX11TestClient
{
  GSubprocess *subprocess;
  char *path;
  gboolean finished;
};

static char *
get_test_client_path (const char *test_client_name)
{
  return g_test_build_filename (G_TEST_BUILT,
                                "x11-test-clients",
                                test_client_name,
                                NULL);
}

static void
x11_test_client_finished (GObject      *source_object,
                          GAsyncResult *res,
                          gpointer      user_data)
{
  MetaX11TestClient *x11_test_client = user_data;
  GError *error = NULL;

  if (!g_subprocess_wait_finish (x11_test_client->subprocess,
                                 res,
                                 &error))
    {
      g_error ("Failed to wait for X11 test client '%s': %s",
               x11_test_client->path, error->message);
    }

  g_assert_true (g_subprocess_get_successful (x11_test_client->subprocess));

  x11_test_client->finished = TRUE;
}

MetaX11TestClient *
meta_x11_test_client_new_with_args (MetaContext *context,
                                    const char  *test_client_name,
                                    ...)
{
  MetaWaylandCompositor *compositor;
  const char *x11_display_name;
  g_autofree char *test_client_path = NULL;
  g_autoptr (GSubprocessLauncher) launcher = NULL;
  GSubprocess *subprocess;
  GError *error = NULL;
  g_autoptr (GPtrArray) args = NULL;
  const gchar *arg;
  va_list ap;
  MetaX11TestClient *x11_test_client;

  compositor = meta_context_get_wayland_compositor (context);
  x11_display_name = meta_wayland_get_public_xwayland_display_name (compositor);
  test_client_path = get_test_client_path (test_client_name);

  launcher = g_subprocess_launcher_new (G_SUBPROCESS_FLAGS_NONE);
  g_subprocess_launcher_setenv (launcher,
                                "DISPLAY", x11_display_name,
                                TRUE);
  g_subprocess_launcher_setenv (launcher,
                                "G_MESSAGES_DEBUG", "all",
                                TRUE);

  va_start (ap, test_client_name);
  args = g_ptr_array_new ();
  g_ptr_array_add (args, (char *) test_client_path);

  while ((arg = va_arg (ap, const gchar *)))
    g_ptr_array_add (args, (gchar *) arg);

  g_ptr_array_add (args, NULL);
  va_end (ap);

  subprocess = g_subprocess_launcher_spawnv (launcher,
                                             (const gchar * const *) args->pdata,
                                             &error);
  if (!subprocess)
    {
      g_error ("Failed to launch Wayland test client '%s': %s",
               test_client_path, error->message);
    }


  x11_test_client = g_new0 (MetaX11TestClient, 1);
  x11_test_client->subprocess = subprocess;
  x11_test_client->path = g_strdup (test_client_name);

  g_subprocess_wait_async (x11_test_client->subprocess, NULL,
                           x11_test_client_finished,
                           x11_test_client);

  return x11_test_client;
}

static void
meta_x11_test_client_free (MetaX11TestClient *x11_test_client)
{
  g_free (x11_test_client->path);
  g_object_unref (x11_test_client->subprocess);
  g_free (x11_test_client);
}

void
meta_x11_test_client_finish (MetaX11TestClient *x11_test_client)
{
  while (!x11_test_client->finished)
    g_main_context_iteration (NULL, TRUE);

  meta_x11_test_client_free (x11_test_client);
}

void
meta_x11_test_client_send_sigterm (MetaX11TestClient *x11_test_client)
{
  g_subprocess_send_signal (x11_test_client->subprocess, SIGTERM);
}
