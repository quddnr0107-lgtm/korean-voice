/* Link-only observation of the original CLI. Engine sources remain unchanged.
 * GNU ld --wrap redirects the CLI's external calls to these two functions.
 * Timing excludes provenance printing; no tensors, logits, or samples are changed.
 */
#define _POSIX_C_SOURCE 200809L
#include "qwen_tts.h"
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <sys/resource.h>

qwen_tts_ctx_t *__real_qwen_tts_load_ex(const char *, int, int, int);
int __real_qwen_tts_generate(qwen_tts_ctx_t *, const char *, float **, int *);
static double load_s = -1.0, tts_s = -1.0;
static int int8_requested = 0;

static double now_s(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}

static void report(const char *stage, const qwen_tts_ctx_t *ctx, int samples, int rc) {
    const char *path = getenv("BORA_BENCH_METRICS");
    if (!path) return;
    char temp[4096];
    if (snprintf(temp, sizeof temp, "%s.tmp", path) >= (int)sizeof temp) exit(70);
    FILE *f = fopen(temp, "w");
    if (!f) exit(70);
    struct rusage usage;
    getrusage(RUSAGE_SELF, &usage);
    fprintf(f, "{\n  \"stage\": \"%s\", \"pid\": %d,\n"
               "  \"cold_model_load_seconds\": %.9f, \"tts_seconds\": %.9f,\n"
               "  \"generated_frames\": %d, \"max_tokens\": %d,\n"
               "  \"samples\": %d, \"sample_rate\": 24000,\n"
               "  \"native_return_code\": %d, \"threads_resolved\": %d,\n"
               "  \"int8_requested\": %s, \"max_rss_kib_observed\": %ld\n}\n",
            stage, (int)getpid(), load_s, tts_s,
            ctx ? ctx->codec_frames : 0, ctx ? ctx->max_tokens : 0,
            samples, rc, qwen_get_threads(), int8_requested ? "true" : "false",
            usage.ru_maxrss);
    if (fclose(f) != 0 || rename(temp, path) != 0) exit(70);
}

qwen_tts_ctx_t *__wrap_qwen_tts_load_ex(const char *dir, int silent, int int8, int int4) {
    int8_requested = int8;
    double start = now_s();
    qwen_tts_ctx_t *ctx = __real_qwen_tts_load_ex(dir, silent, int8, int4);
    load_s = now_s() - start;
    report(ctx ? "loaded" : "load_failed", ctx, 0, ctx ? 0 : 1);
    /* Same process, after its thread and precision configuration was applied. */
    qwen_caps_report(stderr);
    qwen_dispatch_map_report(stderr, getenv("QWEN_DISPATCH_JSON"));
    fprintf(stderr, "[BORA_OBSERVER] pid=%d requested=%s threads=%d load_s=%.9f\n",
            (int)getpid(), int8 ? "int8" : "bf16", qwen_get_threads(), load_s);
    return ctx;
}

int __wrap_qwen_tts_generate(qwen_tts_ctx_t *ctx, const char *text,
                           float **samples, int *count) {
    double start = now_s();
    int rc = __real_qwen_tts_generate(ctx, text, samples, count);
    tts_s = now_s() - start;
    report(rc == 0 ? "generated" : "generation_failed", ctx, count ? *count : 0, rc);
    return rc;
}
