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

#include "compositor/meta-background-effect.h"

#include <math.h>

#include "backends/meta-stage-private.h"
#include "clutter/clutter-mutter.h"
#include "clutter/clutter-paint-node-private.h"

struct _MetaBackgroundBlur
{
  ClutterActor *actor;
  MtkRegion *sample_region;
  MetaStageRedrawClipFilter *redraw_clip_filter;
};

static int
calculate_blur_sample_padding (float radius)
{
  return (int) ceilf (radius * 2.0f);
}

static MtkRegion *
transform_region_to_stage (ClutterActor    *actor,
                           MetaStage       *stage,
                           const MtkRegion *region)
{
  graphene_matrix_t actor_to_stage;

  clutter_actor_get_relative_transformation_matrix (actor,
                                                    CLUTTER_ACTOR (stage),
                                                    &actor_to_stage);
  if (!graphene_matrix_is_2d (&actor_to_stage))
    return NULL;

  return mtk_region_apply_matrix_transform_expand (region, &actor_to_stage);
}

static gboolean
regions_intersect (const MtkRegion *a,
                   const MtkRegion *b)
{
  g_autoptr (MtkRegion) intersection = NULL;

  intersection = mtk_region_copy (a);
  mtk_region_intersect (intersection, b);

  return !mtk_region_is_empty (intersection);
}

static gboolean
union_region_changed (MtkRegion       *region,
                      const MtkRegion *other)
{
  g_autoptr (MtkRegion) old_region = NULL;

  old_region = mtk_region_copy (region);
  mtk_region_union (region, other);

  return !mtk_region_equal (old_region, region);
}

static gboolean
meta_background_blur_expand_redraw_clip (MetaStage        *stage,
                                         ClutterStageView *stage_view,
                                         MtkRegion        *redraw_clip,
                                         gpointer          user_data)
{
  MetaBackgroundBlur *blur = user_data;
  g_autoptr (MtkRegion) sample_region = NULL;
  MtkRectangle view_layout;

  if (clutter_actor_get_stage (blur->actor) != CLUTTER_ACTOR (stage))
    return FALSE;

  if (!clutter_actor_is_effectively_on_stage_view (blur->actor, stage_view))
    return FALSE;

  clutter_stage_view_get_layout (stage_view, &view_layout);
  sample_region = transform_region_to_stage (blur->actor,
                                             stage,
                                             blur->sample_region);
  if (!sample_region)
    return FALSE;

  mtk_region_intersect_rectangle (sample_region, &view_layout);
  if (mtk_region_is_empty (sample_region))
    return FALSE;

  if (!regions_intersect (redraw_clip, sample_region))
    return FALSE;

  return union_region_changed (redraw_clip, sample_region);
}

MetaBackgroundBlur *
meta_background_blur_new (ClutterActor    *actor,
                          const MtkRegion *sample_region)
{
  ClutterActor *stage_actor;
  MetaBackgroundBlur *blur;

  g_return_val_if_fail (CLUTTER_IS_ACTOR (actor), NULL);
  g_return_val_if_fail (sample_region != NULL, NULL);

  stage_actor = clutter_actor_get_stage (actor);
  if (!stage_actor)
    return NULL;

  g_return_val_if_fail (META_IS_STAGE (stage_actor), NULL);

  blur = g_new0 (MetaBackgroundBlur, 1);
  blur->actor = actor;
  blur->sample_region = mtk_region_copy (sample_region);
  blur->redraw_clip_filter =
    meta_stage_add_redraw_clip_filter (META_STAGE (stage_actor),
                                       meta_background_blur_expand_redraw_clip,
                                       blur,
                                       NULL);

  return blur;
}

void
meta_background_blur_destroy (MetaBackgroundBlur *blur)
{
  g_clear_pointer (&blur->redraw_clip_filter,
                   meta_stage_remove_redraw_clip_filter);
  g_clear_pointer (&blur->sample_region, mtk_region_unref);
  g_free (blur);
}

MtkRegion *
meta_background_effect_create_blur_sample_region (const MtkRegion *blur_region,
                                                  float            radius)
{
  g_autoptr (MtkRegion) sample_region = NULL;
  int padding;
  int n_rects;

  sample_region = mtk_region_create ();
  padding = calculate_blur_sample_padding (radius);
  n_rects = mtk_region_num_rectangles (blur_region);

  for (int i = 0; i < n_rects; i++)
    {
      MtkRectangle rect;
      MtkRectangle sample_rect;

      rect = mtk_region_get_rectangle (blur_region, i);
      sample_rect = (MtkRectangle) {
        .x = rect.x - padding,
        .y = rect.y - padding,
        .width = rect.width + 2 * padding,
        .height = rect.height + 2 * padding,
      };

      mtk_region_union_rectangle (sample_region, &sample_rect);
    }

  return g_steal_pointer (&sample_region);
}

static gboolean
actor_box_to_stage_rect (const ClutterActorBox *actor_box,
                         graphene_matrix_t     *actor_to_stage,
                         MtkRectangle          *stage_rect)
{
  graphene_rect_t actor_rect;
  graphene_rect_t transformed_rect;

  graphene_rect_init (&actor_rect,
                      actor_box->x1,
                      actor_box->y1,
                      actor_box->x2 - actor_box->x1,
                      actor_box->y2 - actor_box->y1);
  graphene_matrix_transform_bounds (actor_to_stage,
                                    &actor_rect,
                                    &transformed_rect);
  graphene_rect_round_extents (&transformed_rect, &transformed_rect);

  *stage_rect = (MtkRectangle) {
    .x = (int) transformed_rect.origin.x,
    .y = (int) transformed_rect.origin.y,
    .width = (int) transformed_rect.size.width,
    .height = (int) transformed_rect.size.height,
  };

  return stage_rect->width > 0 && stage_rect->height > 0;
}

static gboolean
stage_rect_to_actor_box (const MtkRectangle *stage_rect,
                         graphene_matrix_t  *stage_to_actor,
                         ClutterActorBox    *actor_box)
{
  graphene_rect_t stage_graphene_rect;
  graphene_rect_t actor_graphene_rect;

  stage_graphene_rect = mtk_rectangle_to_graphene_rect (stage_rect);
  graphene_matrix_transform_bounds (stage_to_actor,
                                    &stage_graphene_rect,
                                    &actor_graphene_rect);

  *actor_box = (ClutterActorBox) {
    .x1 = actor_graphene_rect.origin.x,
    .y1 = actor_graphene_rect.origin.y,
    .x2 = actor_graphene_rect.origin.x + actor_graphene_rect.size.width,
    .y2 = actor_graphene_rect.origin.y + actor_graphene_rect.size.height,
  };

  return actor_box->x2 > actor_box->x1 && actor_box->y2 > actor_box->y1;
}

static gboolean
region_rect_to_actor_box (const MtkRectangle    *region_rect,
                          const ClutterActorBox *content_box,
                          int                    content_width,
                          int                    content_height,
                          ClutterActorBox       *actor_box)
{
  float x_scale;
  float y_scale;

  if (content_width <= 0 || content_height <= 0)
    return FALSE;

  x_scale = (content_box->x2 - content_box->x1) / content_width;
  y_scale = (content_box->y2 - content_box->y1) / content_height;

  *actor_box = (ClutterActorBox) {
    .x1 = content_box->x1 + region_rect->x * x_scale,
    .y1 = content_box->y1 + region_rect->y * y_scale,
    .x2 = content_box->x1 + (region_rect->x + region_rect->width) * x_scale,
    .y2 = content_box->y1 + (region_rect->y + region_rect->height) * y_scale,
  };

  return actor_box->x2 > actor_box->x1 && actor_box->y2 > actor_box->y1;
}

static gboolean
expand_stage_rect_for_blur (const MtkRectangle *stage_rect,
                            const MtkRectangle *view_layout,
                            float               radius,
                            MtkRectangle       *sample_rect)
{
  int padding;

  padding = calculate_blur_sample_padding (radius);

  *sample_rect = (MtkRectangle) {
    .x = stage_rect->x - padding,
    .y = stage_rect->y - padding,
    .width = stage_rect->width + 2 * padding,
    .height = stage_rect->height + 2 * padding,
  };

  return mtk_rectangle_intersect (sample_rect, view_layout, sample_rect);
}

static ClutterPaintNode *
create_blur_node (CoglFramebuffer     *framebuffer,
                  const MtkRectangle  *view_layout,
                  float                view_scale,
                  const MtkRectangle  *source_rect,
                  float                radius,
                  float                saturation,
                  float                noise,
                  uint8_t              opacity)
{
  int source_x;
  int source_y;
  int source_width;
  int source_height;

  source_x = (int) roundf ((source_rect->x - view_layout->x) * view_scale);
  source_y = (int) roundf ((source_rect->y - view_layout->y) * view_scale);
  source_width = MAX (1, (int) roundf (source_rect->width * view_scale));
  source_height = MAX (1, (int) roundf (source_rect->height * view_scale));

  return clutter_blur_node_new_from_framebuffer (framebuffer,
                                                 source_x,
                                                 source_y,
                                                 source_width,
                                                 source_height,
                                                 radius * view_scale,
                                                 saturation,
                                                 noise,
                                                 opacity);
}

static void
add_blur_rectangle (ClutterPaintNode      *paint_node,
                    const MtkRectangle    *source_rect,
                    const MtkRectangle    *stage_rect,
                    const ClutterActorBox *actor_box)
{
  float texture_x1;
  float texture_y1;
  float texture_x2;
  float texture_y2;

  texture_x1 =
    CLAMP ((stage_rect->x - source_rect->x) / (float) source_rect->width,
           0.0f,
           1.0f);
  texture_y1 =
    CLAMP ((stage_rect->y - source_rect->y) / (float) source_rect->height,
           0.0f,
           1.0f);
  texture_x2 =
    CLAMP ((stage_rect->x + stage_rect->width - source_rect->x) /
           (float) source_rect->width,
           0.0f,
           1.0f);
  texture_y2 =
    CLAMP ((stage_rect->y + stage_rect->height - source_rect->y) /
           (float) source_rect->height,
           0.0f,
           1.0f);

  clutter_paint_node_add_texture_rectangle (paint_node,
                                            actor_box,
                                            texture_x1,
                                            texture_y1,
                                            texture_x2,
                                            texture_y2);
}

void
meta_background_effect_paint_blur_region (ClutterPaintNode       *root_node,
                                          ClutterActor           *actor,
                                          ClutterPaintContext    *paint_context,
                                          const ClutterActorBox  *content_box,
                                          int                     content_width,
                                          int                     content_height,
                                          const MtkRegion        *blur_region,
                                          const MtkRegion        *clip_region,
                                          float                   radius,
                                          float                   saturation,
                                          float                   noise,
                                          uint8_t                 opacity)
{
  ClutterActor *stage;
  ClutterStageView *stage_view;
  CoglFramebuffer *source_framebuffer;
  graphene_matrix_t actor_to_stage;
  graphene_matrix_t stage_to_actor;
  ClutterActorBox extents_actor_box;
  MtkRectangle content_rect;
  MtkRectangle effect_extents;
  MtkRectangle extents_stage_rect;
  MtkRectangle source_rect;
  MtkRectangle view_layout;
  g_autoptr (MtkRegion) effect_region = NULL;
  g_autoptr (MtkRegion) paint_region = NULL;
  g_autoptr (ClutterPaintNode) blur_node = NULL;
  float view_scale;
  int n_rects;
  gboolean has_blur_rects = FALSE;

  if (opacity == 0)
    return;

  if (clutter_actor_is_in_clone_paint (actor))
    return;

  stage = clutter_actor_get_stage (actor);
  stage_view = clutter_paint_context_get_stage_view (paint_context);
  if (!stage || !stage_view)
    return;

  source_framebuffer = clutter_stage_view_get_framebuffer (stage_view);
  if (!source_framebuffer)
    return;

  clutter_actor_get_relative_transformation_matrix (actor,
                                                    stage,
                                                    &actor_to_stage);
  if (!graphene_matrix_is_2d (&actor_to_stage) ||
      !graphene_matrix_inverse (&actor_to_stage, &stage_to_actor))
    return;

  content_rect = (MtkRectangle) {
    .width = content_width,
    .height = content_height,
  };

  effect_region = mtk_region_copy (blur_region);
  mtk_region_intersect_rectangle (effect_region, &content_rect);

  if (mtk_region_is_empty (effect_region))
    return;

  paint_region = mtk_region_copy (effect_region);
  if (clip_region)
    mtk_region_intersect (paint_region, clip_region);

  if (mtk_region_is_empty (paint_region))
    return;

  clutter_stage_view_get_layout (stage_view, &view_layout);
  view_scale = clutter_stage_view_get_scale (stage_view);
  effect_extents = mtk_region_get_extents (effect_region);

  if (!region_rect_to_actor_box (&effect_extents,
                                 content_box,
                                 content_width,
                                 content_height,
                                 &extents_actor_box))
    return;

  if (!actor_box_to_stage_rect (&extents_actor_box,
                                &actor_to_stage,
                                &extents_stage_rect))
    return;

  if (!mtk_rectangle_intersect (&extents_stage_rect,
                                &view_layout,
                                &extents_stage_rect))
    return;

  if (!expand_stage_rect_for_blur (&extents_stage_rect,
                                   &view_layout,
                                   radius,
                                   &source_rect))
    return;

  blur_node = create_blur_node (source_framebuffer,
                                &view_layout,
                                view_scale,
                                &source_rect,
                                radius,
                                saturation,
                                noise,
                                opacity);
  if (!blur_node)
    return;

  n_rects = mtk_region_num_rectangles (paint_region);
  for (int i = 0; i < n_rects; i++)
    {
      MtkRectangle region_rect;
      MtkRectangle stage_rect;
      ClutterActorBox actor_box;

      region_rect = mtk_region_get_rectangle (paint_region, i);
      if (!region_rect_to_actor_box (&region_rect,
                                     content_box,
                                     content_width,
                                     content_height,
                                     &actor_box))
        continue;

      if (!actor_box_to_stage_rect (&actor_box,
                                    &actor_to_stage,
                                    &stage_rect))
        continue;

      if (!mtk_rectangle_intersect (&stage_rect, &view_layout, &stage_rect))
        continue;

      if (!stage_rect_to_actor_box (&stage_rect, &stage_to_actor, &actor_box))
        continue;

      add_blur_rectangle (blur_node,
                          &source_rect,
                          &stage_rect,
                          &actor_box);
      has_blur_rects = TRUE;
    }

  if (has_blur_rects)
    clutter_paint_node_add_child (root_node, blur_node);
}
