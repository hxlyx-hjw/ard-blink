#include "line_follower_uart.h"

namespace {

// 模式命令：0=手动，1=自动发送状态，2=自动发送模拟值，3=自动发送阈值
const uint8_t CMD_MANUAL_MODE = 0x00u;
// 手动模式下读 8 通道状态
const uint8_t CMD_READ_STATE = 0x01u;
const uint32_t READ_TIMEOUT_MS = 20u;

}  // namespace

void LineFollower8CHUart::begin(Stream *stream) {
    stream_ = stream;
    data_.activeLevel = LF_ACTIVE_LEVEL;
    if (stream_ == 0) {
        return;
    }

    // 清空残留数据，再进入手动模式
    while (stream_->available()) {
        stream_->read();
    }
    stream_->write(CMD_MANUAL_MODE);
    delay(20);
}

int LineFollower8CHUart::update() {
    if (stream_ == 0) {
        return 0;
    }

    // 丢弃上一条未读数据，保持帧同步
    while (stream_->available()) {
        stream_->read();
    }

    stream_->write(CMD_READ_STATE);

    uint32_t t0 = millis();
    while (stream_->available() < 1) {
        if ((uint32_t)(millis() - t0) > READ_TIMEOUT_MS) {
            return 0;
        }
    }

    uint8_t mask = (uint8_t)stream_->read();
    LineFollower8CH_SetMask(&data_, mask);
    return 1;
}
