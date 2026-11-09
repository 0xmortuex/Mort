/* String workload, the C reference for strings.mx: the same steps and the same allocation pattern. */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    char *data;
    uint64_t length;
} Str;

void *checked(void *pointer) {
    if (!pointer) {
        abort();
    }
    return pointer;
}

uint64_t count_digits(uint64_t value) {
    uint64_t count = 1;
    while (value >= 10) {
        value /= 10;
        count++;
    }
    return count;
}

Str from_u64(uint64_t value) {
    uint64_t count = count_digits(value);
    char *data = checked(malloc(count + 1));
    data[count] = 0;
    for (uint64_t i = count; i > 0; i--) {
        data[i - 1] = (char)('0' + value % 10);
        value /= 10;
    }
    return (Str){data, count};
}

Str from(const char *text) {
    uint64_t length = strlen(text);
    char *data = checked(malloc(length + 1));
    memcpy(data, text, length + 1);
    return (Str){data, length};
}

void append(Str *value, const char *text, uint64_t length) {
    uint64_t total = value->length + length;
    char *data = checked(malloc(total + 1));
    memcpy(data, value->data, value->length);
    memcpy(data + value->length, text, length);
    data[total] = 0;
    free(value->data);
    value->data = data;
    value->length = total;
}

typedef struct {
    const char *data;
    uint64_t length;
} View;

typedef struct {
    View *data;
    uint64_t length;
    uint64_t capacity;
} Parts;

void push(Parts *parts, View item) {
    if (parts->length == parts->capacity) {
        uint64_t capacity = parts->capacity * 2;
        View *data = checked(malloc(capacity * sizeof(View)));
        memcpy(data, parts->data, parts->length * sizeof(View));
        free(parts->data);
        parts->data = data;
        parts->capacity = capacity;
    }
    parts->data[parts->length++] = item;
}

Parts split(Str text) {
    Parts parts = {checked(malloc(4 * sizeof(View))), 0, 4};
    uint64_t start = 0;
    for (uint64_t index = 0; index < text.length; index++) {
        if (text.data[index] == '-') {
            push(&parts, (View){text.data + start, index - start});
            start = index + 1;
        }
    }
    push(&parts, (View){text.data + start, text.length - start});
    return parts;
}

int contains_99(Str text) {
    for (uint64_t i = 0; i + 2 <= text.length; i++) {
        if (text.data[i] == '9' && text.data[i + 1] == '9') {
            return 1;
        }
    }
    return 0;
}

Str to_upper(Str text) {
    char *data = checked(malloc(text.length + 1));
    for (uint64_t i = 0; i < text.length; i++) {
        char c = text.data[i];
        data[i] = (char)((c >= 'a' && c <= 'z') ? c - 32 : c);
    }
    data[text.length] = 0;
    return (Str){data, text.length};
}

int main(int argc, char **argv) {
    (void)argc;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint64_t modulus = 2147483647;
    uint64_t h = 7, hits = 0, total = 0;
    for (uint64_t i = 0; i < n; i++) {
        Str number = from_u64(i);
        Str scaled = from_u64(i * 7);
        Str text = from("item-");
        append(&text, number.data, number.length);
        append(&text, "-", 1);
        append(&text, scaled.data, scaled.length);
        Parts parts = split(text);
        if (contains_99(text)) {
            hits++;
        }
        Str upper = to_upper(text);
        total += upper.length + number.length;
        for (uint64_t index = 0; index < upper.length; index++) {
            h = (h * 31 + (uint8_t)upper.data[index]) % modulus;
        }
        free(parts.data);
        free(upper.data);
        free(text.data);
        free(scaled.data);
        free(number.data);
    }
    printf("%" PRIu64 "\n%" PRIu64 "\n%" PRIu64 "\n", h, hits, total);
    return 0;
}
