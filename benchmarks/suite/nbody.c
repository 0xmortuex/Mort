/* N-body simulation, the C reference for nbody.mx: same algorithm, same flat arrays. */
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

double energy(double *x, double *y, double *z, double *vx, double *vy, double *vz, double *m) {
    double e = 0.0;
    for (uint64_t i = 0; i < 5; i++) {
        e = e + 0.5 * m[i] * (vx[i] * vx[i] + vy[i] * vy[i] + vz[i] * vz[i]);
        for (uint64_t j = i + 1; j < 5; j++) {
            double dx = x[i] - x[j];
            double dy = y[i] - y[j];
            double dz = z[i] - z[j];
            e = e - m[i] * m[j] / sqrt(dx * dx + dy * dy + dz * dz);
        }
    }
    return e;
}

void advance(double *x, double *y, double *z, double *vx, double *vy, double *vz, double *m, double dt) {
    for (uint64_t i = 0; i < 5; i++) {
        for (uint64_t j = i + 1; j < 5; j++) {
            double dx = x[i] - x[j];
            double dy = y[i] - y[j];
            double dz = z[i] - z[j];
            double d2 = dx * dx + dy * dy + dz * dz;
            double mag = dt / (d2 * sqrt(d2));
            double mi = m[i] * mag;
            double mj = m[j] * mag;
            vx[i] = vx[i] - dx * mj;
            vy[i] = vy[i] - dy * mj;
            vz[i] = vz[i] - dz * mj;
            vx[j] = vx[j] + dx * mi;
            vy[j] = vy[j] + dy * mi;
            vz[j] = vz[j] + dz * mi;
        }
    }
    for (uint64_t i = 0; i < 5; i++) {
        x[i] = x[i] + dt * vx[i];
        y[i] = y[i] + dt * vy[i];
        z[i] = z[i] + dt * vz[i];
    }
}

int main(int argc, char **argv) {
    (void)argc;
    uint64_t steps = strtoull(argv[1], NULL, 10);
    double solar = 39.47841760435743;
    double days = 365.24;
    double x[5] = {0.0, 4.84143144246472082945, 8.34336671801458124085, 12.8943695621391310164, 15.3796971148509165498};
    double y[5] = {0.0, -1.16032004402742839362, 4.12479856412430479603, -15.1111514016986312525, -25.9193146099879641146};
    double z[5] = {0.0, -0.103622044471123109503, -0.403523417825932481042, -0.223307578892655733682, 0.179258772950371181758};
    double vx[5] = {0.0, 0.00166007664274403694959 * days, -0.00276742510726862411004 * days, 0.00296460137564761618652 * days, 0.00268067772490389322511 * days};
    double vy[5] = {0.0, 0.00769901118419740425080 * days, 0.00499852801234917238954 * days, 0.00237847173959480950018 * days, 0.00162824170038242295922 * days};
    double vz[5] = {0.0, -0.0000690460016972063023554 * days, 0.0000230417297573763262252 * days, -0.0000296589568540237556131 * days, -0.0000951592254519715870971 * days};
    double m[5] = {solar, 0.000954791938424326609124 * solar, 0.000285885980666130812119 * solar, 0.0000436624404335156298006 * solar, 0.0000515138902046611451304 * solar};
    double px = 0.0, py = 0.0, pz = 0.0;
    for (uint64_t i = 0; i < 5; i++) {
        px = px + vx[i] * m[i];
        py = py + vy[i] * m[i];
        pz = pz + vz[i] * m[i];
    }
    vx[0] = 0.0 - px / solar;
    vy[0] = 0.0 - py / solar;
    vz[0] = 0.0 - pz / solar;
    printf("%" PRId64 "\n", (int64_t)(energy(x, y, z, vx, vy, vz, m) * 1000000000.0));
    for (uint64_t step = 0; step < steps; step++) {
        advance(x, y, z, vx, vy, vz, m, 0.01);
    }
    printf("%" PRId64 "\n", (int64_t)(energy(x, y, z, vx, vy, vz, m) * 1000000000.0));
    return 0;
}
