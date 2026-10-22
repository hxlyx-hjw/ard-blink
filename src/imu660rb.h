#ifndef IMU660RB_H
#define IMU660RB_H

#include <stdint.h>

// IMU660RB（内部芯片 ST LSM6DSR，6 轴 IMU）驱动接口
typedef struct {
    uint8_t addr;      // 探测到的 I2C 地址（0x6A 或 0x6B）
    float ax, ay, az;  // 加速度，单位 g
    float gx, gy, gz;  // 角速度，单位 °/s (dps)
} Imu660RB;

// 初始化 I2C 并识别芯片，成功返回 1，失败返回 0
int Imu660RB_Init(Imu660RB *imu);

// 读取并换算最新数据，有新数据返回 1，暂无数据返回 0
int Imu660RB_Update(Imu660RB *imu);

#endif  // IMU660RB_H
