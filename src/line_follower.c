#include "line_follower.h"

// Arduino 核心以 C 链接方式（Arduino.h 内 extern "C"）提供这些函数，
// 因此纯 C 文件可以直接声明并调用。
void pinMode(uint8_t pin, uint8_t mode);
void digitalWrite(uint8_t pin, uint8_t val);
int digitalRead(uint8_t pin);

#define PIN_MODE_INPUT  0x00u
#define PIN_MODE_OUTPUT 0x01u

void LineFollower8CH_Init(LineFollower8CH *self,
                          const uint8_t pins[LF_CHANNELS],
                          uint8_t irEnablePin) {
    uint8_t i;
    if (self == 0) {
        return;
    }

    for (i = 0; i < LF_CHANNELS; i++) {
        self->pins[i] = pins[i];
        pinMode(pins[i], PIN_MODE_INPUT);
    }

    self->irEnablePin = irEnablePin;
    self->activeLevel = LF_ACTIVE_LEVEL;
    self->mask = 0u;
    self->count = 0u;
    self->weightedPos = LF_LINE_LOST;

    if (irEnablePin != LF_NO_PIN) {
        pinMode(irEnablePin, PIN_MODE_OUTPUT);
        digitalWrite(irEnablePin, LF_IR_ENABLE_LEVEL);
    }
}

void LineFollower8CH_Update(LineFollower8CH *self) {
    uint8_t i;
    uint8_t mask = 0u;

    if (self == 0) {
        return;
    }

    for (i = 0; i < LF_CHANNELS; i++) {
        if (digitalRead(self->pins[i]) == self->activeLevel) {
            mask |= (uint8_t)(1u << i);
        }
    }

    LineFollower8CH_SetMask(self, mask);
}

void LineFollower8CH_SetMask(LineFollower8CH *self, uint8_t mask) {
    uint8_t i;
    uint8_t count = 0u;
    int32_t sum = 0;

    if (self == 0) {
        return;
    }

    for (i = 0; i < LF_CHANNELS; i++) {
        if (mask & (uint8_t)(1u << i)) {
            count++;
            sum += (int32_t)i * LF_POS_SCALE;
        }
    }

    self->mask = mask;
    self->count = count;
    self->weightedPos = (count > 0u) ? (sum / (int32_t)count) : LF_LINE_LOST;
}

int32_t LineFollower8CH_Position(const LineFollower8CH *self) {
    return (self != 0) ? self->weightedPos : LF_LINE_LOST;
}

int32_t LineFollower8CH_NormalizedPosition(const LineFollower8CH *self) {
    int32_t center;
    if (self == 0 || self->weightedPos == LF_LINE_LOST) {
        return LF_LINE_LOST;
    }

    // 把 0..7000 映射到 -1000..+1000
    center = (int32_t)(LF_CHANNELS - 1u) * LF_POS_SCALE / 2;  // 3500
    return (self->weightedPos - center) * 2 / (int32_t)(LF_CHANNELS - 1u);
}
