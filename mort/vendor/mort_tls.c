#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mbedtls/ctr_drbg.h"
#include "mbedtls/entropy.h"
#include "mbedtls/net_sockets.h"
#include "mbedtls/ssl.h"
#include "mbedtls/x509_crt.h"

#include "mort_ca_bundle.inc"

struct mort_tls_handle {
    mbedtls_net_context net;
    mbedtls_ssl_context ssl;
    mbedtls_ssl_config config;
    mbedtls_x509_crt roots;
    mbedtls_ctr_drbg_context random;
    mbedtls_entropy_context entropy;
};

static void mort_tls_release(struct mort_tls_handle *handle, int notify) {
    if (handle == NULL) {
        return;
    }
    if (notify) {
        int status;
        do {
            status = mbedtls_ssl_close_notify(&handle->ssl);
        } while (status == MBEDTLS_ERR_SSL_WANT_READ ||
                 status == MBEDTLS_ERR_SSL_WANT_WRITE);
    }
    mbedtls_net_free(&handle->net);
    mbedtls_x509_crt_free(&handle->roots);
    mbedtls_ssl_free(&handle->ssl);
    mbedtls_ssl_config_free(&handle->config);
    mbedtls_ctr_drbg_free(&handle->random);
    mbedtls_entropy_free(&handle->entropy);
    free(handle);
}

static void *mort_tls_connect_with_roots(
        const uint8_t *hostname,
        uint16_t port,
        const uint8_t *ca_data,
        uint64_t ca_length) {
    static const unsigned char personalization[] = "mort-tls-client";
    struct mort_tls_handle *handle;
    unsigned char *roots;
    char service[6];
    int status;

    if (hostname == NULL || hostname[0] == 0 || port == 0 ||
            ca_data == NULL || ca_length == 0 ||
            ca_length >= (uint64_t)SIZE_MAX) {
        return NULL;
    }
    if (snprintf(service, sizeof(service), "%u", (unsigned int)port) <= 0) {
        return NULL;
    }
    handle = (struct mort_tls_handle *)calloc(1, sizeof(*handle));
    if (handle == NULL) {
        return NULL;
    }
    mbedtls_net_init(&handle->net);
    mbedtls_ssl_init(&handle->ssl);
    mbedtls_ssl_config_init(&handle->config);
    mbedtls_x509_crt_init(&handle->roots);
    mbedtls_ctr_drbg_init(&handle->random);
    mbedtls_entropy_init(&handle->entropy);

    status = mbedtls_ctr_drbg_seed(
        &handle->random, mbedtls_entropy_func, &handle->entropy,
        personalization, sizeof(personalization) - 1);
    if (status != 0) {
        mort_tls_release(handle, 0);
        return NULL;
    }
    roots = (unsigned char *)malloc((size_t)ca_length + 1);
    if (roots == NULL) {
        mort_tls_release(handle, 0);
        return NULL;
    }
    memcpy(roots, ca_data, (size_t)ca_length);
    roots[ca_length] = 0;
    status = mbedtls_x509_crt_parse(
        &handle->roots, roots, (size_t)ca_length + 1);
    free(roots);
    if (status != 0) {
        mort_tls_release(handle, 0);
        return NULL;
    }
    status = mbedtls_net_connect(
        &handle->net, (const char *)hostname, service,
        MBEDTLS_NET_PROTO_TCP);
    if (status != 0) {
        mort_tls_release(handle, 0);
        return NULL;
    }
    status = mbedtls_ssl_config_defaults(
        &handle->config, MBEDTLS_SSL_IS_CLIENT,
        MBEDTLS_SSL_TRANSPORT_STREAM, MBEDTLS_SSL_PRESET_DEFAULT);
    if (status != 0) {
        mort_tls_release(handle, 0);
        return NULL;
    }
    mbedtls_ssl_conf_authmode(
        &handle->config, MBEDTLS_SSL_VERIFY_REQUIRED);
    mbedtls_ssl_conf_ca_chain(
        &handle->config, &handle->roots, NULL);
    mbedtls_ssl_conf_rng(
        &handle->config, mbedtls_ctr_drbg_random, &handle->random);
    mbedtls_ssl_conf_min_tls_version(
        &handle->config, MBEDTLS_SSL_VERSION_TLS1_2);
    status = mbedtls_ssl_setup(&handle->ssl, &handle->config);
    if (status != 0) {
        mort_tls_release(handle, 0);
        return NULL;
    }
    status = mbedtls_ssl_set_hostname(
        &handle->ssl, (const char *)hostname);
    if (status != 0) {
        mort_tls_release(handle, 0);
        return NULL;
    }
    mbedtls_ssl_set_bio(
        &handle->ssl, &handle->net,
        mbedtls_net_send, mbedtls_net_recv, NULL);
    do {
        status = mbedtls_ssl_handshake(&handle->ssl);
    } while (status == MBEDTLS_ERR_SSL_WANT_READ ||
             status == MBEDTLS_ERR_SSL_WANT_WRITE);
    if (status != 0 || mbedtls_ssl_get_verify_result(&handle->ssl) != 0) {
        mort_tls_release(handle, 0);
        return NULL;
    }
    return handle;
}

void *mort_tls_client_connect(const uint8_t *hostname, uint16_t port) {
    return mort_tls_connect_with_roots(
        hostname, port, mort_ca_bundle, mort_ca_bundle_len - 1);
}

void *mort_tls_client_connect_with_ca(
        const uint8_t *hostname,
        uint16_t port,
        const uint8_t *ca_data,
        uint64_t ca_length) {
    return mort_tls_connect_with_roots(
        hostname, port, ca_data, ca_length);
}

int64_t mort_tls_send(
        void *raw, const uint8_t *buffer, uint64_t length) {
    struct mort_tls_handle *handle = (struct mort_tls_handle *)raw;
    size_t amount;
    int status;

    if (handle == NULL || (buffer == NULL && length != 0)) {
        return -1;
    }
    if (length == 0) {
        return 0;
    }
    amount = length > (uint64_t)INT_MAX ? (size_t)INT_MAX : (size_t)length;
    do {
        status = mbedtls_ssl_write(&handle->ssl, buffer, amount);
    } while (status == MBEDTLS_ERR_SSL_WANT_READ ||
             status == MBEDTLS_ERR_SSL_WANT_WRITE);
    return status < 0 ? -1 : (int64_t)status;
}

int64_t mort_tls_recv(void *raw, uint8_t *buffer, uint64_t length) {
    struct mort_tls_handle *handle = (struct mort_tls_handle *)raw;
    size_t amount;
    int status;

    if (handle == NULL || (buffer == NULL && length != 0)) {
        return -1;
    }
    if (length == 0) {
        return 0;
    }
    amount = length > (uint64_t)INT_MAX ? (size_t)INT_MAX : (size_t)length;
    do {
        status = mbedtls_ssl_read(&handle->ssl, buffer, amount);
    } while (status == MBEDTLS_ERR_SSL_WANT_READ ||
             status == MBEDTLS_ERR_SSL_WANT_WRITE);
    if (status == MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY) {
        return 0;
    }
    if (status == 0) {
        return -1;
    }
    return status < 0 ? -1 : (int64_t)status;
}

uint16_t mort_tls_protocol_version(void *raw) {
    struct mort_tls_handle *handle = (struct mort_tls_handle *)raw;
    const char *version;

    if (handle == NULL) {
        return 0;
    }
    version = mbedtls_ssl_get_version(&handle->ssl);
    if (version != NULL && strcmp(version, "TLSv1.3") == 0) {
        return 13;
    }
    if (version != NULL && strcmp(version, "TLSv1.2") == 0) {
        return 12;
    }
    return 0;
}

void mort_tls_close(void *raw) {
    mort_tls_release((struct mort_tls_handle *)raw, 1);
}
