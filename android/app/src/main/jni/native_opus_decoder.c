#include <jni.h>
#include <stdint.h>

#include "opus.h"

#define SAMPLE_RATE 48000
#define CHANNELS 1
#define FRAME_SAMPLES 480
#define MAX_OPUS_PACKET_BYTES 1275

static void throw_io_exception(JNIEnv *env, const char *message) {
    jclass exception_class = (*env)->FindClass(env, "java/io/IOException");
    if (exception_class != NULL) {
        (*env)->ThrowNew(env, exception_class, message);
    }
}

JNIEXPORT jlong JNICALL
Java_app_monitorize_android_streaming_NativeOpusDecoder_nativeCreate(
        JNIEnv *env, jobject instance) {
    (void) instance;
    int error = OPUS_OK;
    OpusDecoder *decoder = opus_decoder_create(SAMPLE_RATE, CHANNELS, &error);
    if (decoder == NULL || error != OPUS_OK) {
        throw_io_exception(env, opus_strerror(error));
        return 0;
    }
    return (jlong) (intptr_t) decoder;
}

JNIEXPORT jbyteArray JNICALL
Java_app_monitorize_android_streaming_NativeOpusDecoder_nativeDecode(
        JNIEnv *env, jobject instance, jlong handle, jbyteArray packet) {
    (void) instance;
    OpusDecoder *decoder = (OpusDecoder *) (intptr_t) handle;
    if (decoder == NULL) {
        throw_io_exception(env, "native Opus decoder is closed");
        return NULL;
    }

    unsigned char encoded[MAX_OPUS_PACKET_BYTES];
    const unsigned char *encoded_data = NULL;
    opus_int32 encoded_size = 0;
    if (packet != NULL) {
        jsize packet_size = (*env)->GetArrayLength(env, packet);
        if (packet_size <= 0 || packet_size > MAX_OPUS_PACKET_BYTES) {
            throw_io_exception(env, "invalid Opus packet size");
            return NULL;
        }
        (*env)->GetByteArrayRegion(env, packet, 0, packet_size, (jbyte *) encoded);
        if ((*env)->ExceptionCheck(env)) return NULL;
        encoded_data = encoded;
        encoded_size = packet_size;
    }

    opus_int16 samples[FRAME_SAMPLES];
    int decoded = opus_decode(
        decoder, encoded_data, encoded_size, samples, FRAME_SAMPLES, 0
    );
    if (decoded < 0) {
        throw_io_exception(env, opus_strerror(decoded));
        return NULL;
    }

    jbyte pcm[FRAME_SAMPLES * 2];
    for (int index = 0; index < decoded; index++) {
        pcm[index * 2] = (jbyte) (samples[index] & 0xff);
        pcm[index * 2 + 1] = (jbyte) ((samples[index] >> 8) & 0xff);
    }
    jbyteArray output = (*env)->NewByteArray(env, decoded * 2);
    if (output == NULL) return NULL;
    (*env)->SetByteArrayRegion(env, output, 0, decoded * 2, pcm);
    return output;
}

JNIEXPORT jstring JNICALL
Java_app_monitorize_android_streaming_NativeOpusDecoder_nativeVersion(
        JNIEnv *env, jobject instance) {
    (void) instance;
    return (*env)->NewStringUTF(env, opus_get_version_string());
}

JNIEXPORT void JNICALL
Java_app_monitorize_android_streaming_NativeOpusDecoder_nativeDestroy(
        JNIEnv *env, jobject instance, jlong handle) {
    (void) env;
    (void) instance;
    OpusDecoder *decoder = (OpusDecoder *) (intptr_t) handle;
    if (decoder != NULL) opus_decoder_destroy(decoder);
}
