#include <errno.h>
#include <fcntl.h>
#include <linux/gpio.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static long now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return ts.tv_sec * 1000000L + ts.tv_nsec / 1000L;
}

static int get_value(int line_fd) {
    struct gpiohandle_data data;
    memset(&data, 0, sizeof(data));
    if (ioctl(line_fd, GPIOHANDLE_GET_LINE_VALUES_IOCTL, &data) < 0) {
        return -1;
    }
    return data.values[0] ? 1 : 0;
}

static int set_value(int line_fd, int value) {
    struct gpiohandle_data data;
    memset(&data, 0, sizeof(data));
    data.values[0] = value ? 1 : 0;
    return ioctl(line_fd, GPIOHANDLE_SET_LINE_VALUES_IOCTL, &data);
}

static int wait_level(int line_fd, int want, long timeout_us) {
    long start = now_us();
    int value;
    do {
        value = get_value(line_fd);
        if (value < 0) {
            return -1;
        }
        if (value == want) {
            return 0;
        }
    } while (now_us() - start < timeout_us);
    return -2;
}

static int read_dht11(const char *chip_path, unsigned int offset) {
    int chip_fd = -1;
    int line_fd = -1;
    struct gpiohandle_request req;
    struct gpiohandle_config cfg;
    uint8_t data[5] = {0};

    chip_fd = open(chip_path, O_RDONLY);
    if (chip_fd < 0) {
        fprintf(stderr, "open %s failed: %s\n", chip_path, strerror(errno));
        return 2;
    }

    memset(&req, 0, sizeof(req));
    req.lineoffsets[0] = offset;
    req.lines = 1;
    req.flags = GPIOHANDLE_REQUEST_OUTPUT;
    req.default_values[0] = 1;
    snprintf(req.consumer_label, sizeof(req.consumer_label), "labsafe-dht11");
    if (ioctl(chip_fd, GPIO_GET_LINEHANDLE_IOCTL, &req) < 0) {
        fprintf(stderr, "request line %s:%u failed: %s\n", chip_path, offset, strerror(errno));
        close(chip_fd);
        return 3;
    }
    line_fd = req.fd;

    set_value(line_fd, 1);
    usleep(1000);
    set_value(line_fd, 0);
    usleep(20000);
    set_value(line_fd, 1);
    usleep(35);

    memset(&cfg, 0, sizeof(cfg));
    cfg.flags = GPIOHANDLE_REQUEST_INPUT;
    if (ioctl(line_fd, GPIOHANDLE_SET_CONFIG_IOCTL, &cfg) < 0) {
        fprintf(stderr, "switch line to input failed: %s\n", strerror(errno));
        close(line_fd);
        close(chip_fd);
        return 4;
    }

    if (wait_level(line_fd, 0, 2000) < 0 ||
        wait_level(line_fd, 1, 2000) < 0 ||
        wait_level(line_fd, 0, 2000) < 0) {
        fprintf(stderr, "no DHT11 response on %s:%u\n", chip_path, offset);
        close(line_fd);
        close(chip_fd);
        return 5;
    }

    for (int bit = 0; bit < 40; bit++) {
        if (wait_level(line_fd, 1, 1000) < 0) {
            fprintf(stderr, "timeout waiting bit %d high\n", bit);
            close(line_fd);
            close(chip_fd);
            return 6;
        }
        long high_start = now_us();
        int low_wait = wait_level(line_fd, 0, 1000);
        long high_us = now_us() - high_start;
        if (low_wait < 0 && bit < 39) {
            fprintf(stderr, "timeout waiting bit %d low\n", bit);
            close(line_fd);
            close(chip_fd);
            return 7;
        }
        data[bit / 8] <<= 1;
        if (high_us > 50) {
            data[bit / 8] |= 1;
        }
    }

    close(line_fd);
    close(chip_fd);

    uint8_t checksum = (uint8_t)(data[0] + data[1] + data[2] + data[3]);
    if (checksum != data[4]) {
        fprintf(stderr, "checksum failed: raw=%u,%u,%u,%u,%u calc=%u\n",
                data[0], data[1], data[2], data[3], data[4], checksum);
        return 8;
    }

    printf("{\"chip\":\"%s\",\"offset\":%u,\"temperature\":%u.%u,\"humidity\":%u.%u}\n",
           chip_path, offset, data[2], data[3], data[0], data[1]);
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s /dev/gpiochipN offset\n", argv[0]);
        return 1;
    }
    struct sched_param sp;
    memset(&sp, 0, sizeof(sp));
    sp.sched_priority = 50;
    sched_setscheduler(0, SCHED_FIFO, &sp);
    mlockall(MCL_CURRENT | MCL_FUTURE);
    return read_dht11(argv[1], (unsigned int)strtoul(argv[2], NULL, 0));
}
