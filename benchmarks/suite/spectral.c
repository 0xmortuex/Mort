/* Spectral norm, the C reference for spectral.mx. */
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

double a(uint64_t i, uint64_t j) {
    return 1.0 / (double)((i + j) * (i + j + 1) / 2 + i + 1);
}

void times(double *out, double *v, uint64_t n) {
    for (uint64_t i = 0; i < n; i++) {
        double sum = 0.0;
        for (uint64_t j = 0; j < n; j++) {
            sum = sum + a(i, j) * v[j];
        }
        out[i] = sum;
    }
}

void times_transposed(double *out, double *v, uint64_t n) {
    for (uint64_t i = 0; i < n; i++) {
        double sum = 0.0;
        for (uint64_t j = 0; j < n; j++) {
            sum = sum + a(j, i) * v[j];
        }
        out[i] = sum;
    }
}

void normal(double *out, double *v, double *scratch, uint64_t n) {
    times(scratch, v, n);
    times_transposed(out, scratch, n);
}

int main(int argc, char **argv) {
    (void)argc;
    uint64_t n = strtoull(argv[1], NULL, 10);
    double *u = malloc(n * sizeof(double));
    double *v = malloc(n * sizeof(double));
    double *scratch = malloc(n * sizeof(double));
    if (!u || !v || !scratch) {
        return 1;
    }
    for (uint64_t i = 0; i < n; i++) {
        u[i] = 1.0;
    }
    for (int k = 0; k < 10; k++) {
        normal(v, u, scratch, n);
        normal(u, v, scratch, n);
    }
    double vbv = 0.0, vv = 0.0;
    for (uint64_t i = 0; i < n; i++) {
        vbv = vbv + u[i] * v[i];
        vv = vv + v[i] * v[i];
    }
    printf("%" PRId64 "\n", (int64_t)(sqrt(vbv / vv) * 1000000000.0));
    free(u);
    free(v);
    free(scratch);
    return 0;
}
