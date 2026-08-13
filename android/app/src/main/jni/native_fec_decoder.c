#include <jni.h>
#include <android/log.h>
#include <stdlib.h>
#include <string.h>

#include "nanors/rs.h"

#define LOG_TAG "MonitorizeFecJni"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

JNIEXPORT jint JNICALL
Java_app_monitorize_android_streaming_StreamReceiver_nativeReconstructFec(
    JNIEnv *env,
    jobject thiz,
    jint dataShards,
    jint parityShards,
    jobjectArray shardBuffers,
    jbooleanArray shardMarks,
    jint shardSize
) {
    (void)thiz;
    int totalShards = dataShards + parityShards;
    if (totalShards <= 0 || shardSize <= 0) return -1;

    reed_solomon *rs = reed_solomon_new(dataShards, parityShards);
    if (!rs) {
        LOGE("Failed to allocate reed_solomon instance");
        return -1;
    }

    void **shards = calloc(totalShards, sizeof(void*));
    uint8_t *marks = calloc(totalShards, sizeof(uint8_t));
    jbyteArray *byteArrays = calloc(totalShards, sizeof(jbyteArray));

    jboolean *marksElems = (*env)->GetBooleanArrayElements(env, shardMarks, NULL);
    for (int i = 0; i < totalShards; i++) {
        marks[i] = marksElems[i] ? 1 : 0;
        byteArrays[i] = (jbyteArray)(*env)->GetObjectArrayElement(env, shardBuffers, i);
        if (byteArrays[i]) {
            shards[i] = (*env)->GetByteArrayElements(env, byteArrays[i], NULL);
        }
    }

    int res = reed_solomon_reconstruct(rs, (unsigned char **)shards, marks, totalShards, shardSize);

    for (int i = 0; i < totalShards; i++) {
        if (byteArrays[i] && shards[i]) {
            (*env)->ReleaseByteArrayElements(env, byteArrays[i], (jbyte *)shards[i], 0);
        }
        if (byteArrays[i]) {
            (*env)->DeleteLocalRef(env, byteArrays[i]);
        }
    }
    (*env)->ReleaseBooleanArrayElements(env, shardMarks, marksElems, JNI_ABORT);

    free(shards);
    free(marks);
    free(byteArrays);
    reed_solomon_release(rs);

    return res;
}
