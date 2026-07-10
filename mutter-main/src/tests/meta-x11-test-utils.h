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

#pragma once

#include "meta/meta-context.h"

typedef struct _MetaX11TestClient MetaX11TestClient;

MetaX11TestClient * meta_x11_test_client_new_with_args (MetaContext *context,
                                                        const char  *test_client_name,
                                                        ...);

void meta_x11_test_client_finish (MetaX11TestClient *x11_test_client);

void meta_x11_test_client_send_sigterm (MetaX11TestClient *x11_test_client);
