#ifndef LINE_FOLLOWER_8CH_H
#define LINE_FOLLOWER_8CH_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// 通道数量
#define LF_CHANNELS     8u

// 位置换算分辨率（千分位），相对位置范围 -1000..+1000
#define LF_POS_SCALE    1000

// 全部丢线时的返回值
#define LF_LINE_LOST    (-1)

// 未使用某个引脚时的占位值
#define LF_NO_PIN       0xFFu

// ------------------------------------------------------------------
//  有效电平（检测到黑线/目标时通道输出的电平）：
//    1 = 输出高电平（白 LOW / 黑 HIGH，市面上大多数 8 路模块）
//    0 = 输出低电平（黑 LOW / 白 HIGH）
//  可在编译时用 -DLF_ACTIVE_LEVEL=0 覆盖
// ------------------------------------------------------------------
#ifndef LF_ACTIVE_LEVEL
#define LF_ACTIVE_LEVEL 1u
#endif

// 部分模块带“IR 使能”脚，高电平点亮红外发射管；按实际模块调整
#ifndef LF_IR_ENABLE_LEVEL
#define LF_IR_ENABLE_LEVEL 1u
#endif

typedef struct {
    uint8_t  pins[LF_CHANNELS];  // 各通道连接的 MCU 引脚，pins[0] 为最左侧
    uint8_t  irEnablePin;        // IR 使能脚，不用则填 LF_NO_PIN
    uint8_t  activeLevel;        // 有效电平
    uint8_t  mask;               // 8 位掩码：bit i = 第 i 通道是否在线上
    uint8_t  count;              // 当前在线的通道数
    int32_t  weightedPos;        // 加权位置 0..(LF_CHANNELS-1)*LF_POS_SCALE
} LineFollower8CH;

// 初始化：pins 数组长度必须为 LF_CHANNELS
void LineFollower8CH_Init(LineFollower8CH *self,
                          const uint8_t pins[LF_CHANNELS],
                          uint8_t irEnablePin);

// 采样所有通道并更新 mask / count / 加权位置
void LineFollower8CH_Update(LineFollower8CH *self);

// 直接以 8 位掩码更新（供 UART / I2C 等非 GPIO 读取方式复用）
void LineFollower8CH_SetMask(LineFollower8CH *self, uint8_t mask);

// 加权位置（0..7000，中线 3500）；全丢线返回 LF_LINE_LOST
int32_t LineFollower8CH_Position(const LineFollower8CH *self);

// 归一化相对位置（-1000..+1000，0 为中线）；全丢线返回 LF_LINE_LOST
int32_t LineFollower8CH_NormalizedPosition(const LineFollower8CH *self);

#ifdef __cplusplus
}
#endif

#endif  // LINE_FOLLOWER_8CH_H
