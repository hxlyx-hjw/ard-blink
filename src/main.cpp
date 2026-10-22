#include <Arduino.h>

#include "imu660rb.h"
#include "line_follower.h"
#include "line_follower_uart.h"

// 8 路光电通信方式：1 = UART(TX/RX)，0 = GPIO(8 根数字脚)
#define LF_COMM_MODE 1

#if LF_COMM_MODE == 1
#include <SoftwareSerial.h>
#endif

// UART 接线（交叉）：模块 TX -> D2，模块 RX -> D3
#define LF_UART_RX 2
#define LF_UART_TX 3

// GPIO 接线：S1~S8 -> D2~D9（对应 bit0..bit7）
static const uint8_t LF_PINS[LF_CHANNELS] = {2, 3, 4, 5, 6, 7, 8, 9};

#if LF_COMM_MODE == 1
static SoftwareSerial g_lfSerial(LF_UART_RX, LF_UART_TX);
static LineFollower8CHUart g_line;
#else
static LineFollower8CH g_line;
#endif

static Imu660RB g_imu;
static int g_imuOk = 0;

static void printLine(const LineFollower8CH *lf) {
    int32_t norm;

    Serial.print("mask=");
    for (int i = LF_CHANNELS - 1; i >= 0; i--) {
        Serial.print((lf->mask >> i) & 1);
    }
    Serial.print(" on=");
    Serial.print(lf->count);

    norm = LineFollower8CH_NormalizedPosition(lf);
    if (norm == LF_LINE_LOST) {
        Serial.print(" pos=LOST");
    } else {
        Serial.print(" pos=");
        Serial.print(norm / 1000.0f, 3);
    }
}

void setup() {
    Serial.begin(115200);

#if LF_COMM_MODE == 1
    g_lfSerial.begin(115200);
    g_line.begin(&g_lfSerial);
#else
    LineFollower8CH_Init(&g_line, LF_PINS, LF_NO_PIN);
#endif

    // IMU 初始化（IMU 未接好也不影响光电部分继续输出）
    g_imuOk = Imu660RB_Init(&g_imu);
    if (!g_imuOk) {
        Serial.println("[IMU] not found, line follower only");
    }

    Serial.println("line mask=... on=N pos=-1.0..+1.0 | imu ax ay az gx gy gz");
}

void loop() {
#if LF_COMM_MODE == 1
    g_line.update();
    printLine(&g_line.data());
#else
    LineFollower8CH_Update(&g_line);
    printLine(&g_line);
#endif

    if (g_imuOk && Imu660RB_Update(&g_imu)) {
        Serial.print(" | imu ");
        Serial.print(g_imu.ax, 3); Serial.print(' ');
        Serial.print(g_imu.ay, 3); Serial.print(' ');
        Serial.print(g_imu.az, 3); Serial.print(' ');
        Serial.print(g_imu.gx, 3); Serial.print(' ');
        Serial.print(g_imu.gy, 3); Serial.print(' ');
        Serial.print(g_imu.gz, 3);
    }

    Serial.println();
    delay(5);  // 约 100+ Hz（串口 115200 打印是实际瓶颈）
}
