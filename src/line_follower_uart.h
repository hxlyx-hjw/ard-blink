#ifndef LINE_FOLLOWER_8CH_UART_H
#define LINE_FOLLOWER_8CH_UART_H

#include <Arduino.h>

#include "line_follower.h"

// 基于 UART(TX/RX) 的 8 路光电驱动，协议兼容 Hiwonder 8-ch Line Follower：
//   上电发 0x00 进入手动模式；读状态发 0x01，模块返回 1 字节（bit i = 第 i 通道）
class LineFollower8CHUart {
public:
    LineFollower8CHUart() : stream_(0) {}

    // 传入已 begin 好的 SoftwareSerial 或 HardwareSerial
    void begin(Stream *stream);

    // 请求一次状态并解析，成功返回 1，超时返回 0
    int update();

    const LineFollower8CH &data() const { return data_; }

private:
    Stream *stream_;
    LineFollower8CH data_;
};

#endif  // LINE_FOLLOWER_8CH_UART_H
