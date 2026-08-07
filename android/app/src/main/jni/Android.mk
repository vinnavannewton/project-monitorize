LOCAL_PATH := $(call my-dir)

include $(CLEAR_VARS)
LOCAL_MODULE := opus
LOCAL_SRC_FILES := third_party/libopus/$(TARGET_ARCH_ABI)/libopus.a
LOCAL_EXPORT_C_INCLUDES := $(LOCAL_PATH)/third_party/libopus/include
include $(PREBUILT_STATIC_LIBRARY)

include $(CLEAR_VARS)
LOCAL_MODULE := monitorize_audio
LOCAL_SRC_FILES := native_opus_decoder.c
LOCAL_STATIC_LIBRARIES := opus
LOCAL_LDLIBS := -llog
LOCAL_LDFLAGS := -Wl,-z,max-page-size=16384
include $(BUILD_SHARED_LIBRARY)
