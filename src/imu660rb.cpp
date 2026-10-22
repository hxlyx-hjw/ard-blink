#include <Arduino.h>
#include <Wire.h>

#include "imu660rb.h"

namespace {

// LSM6DSR 的 I2C 地址由 SA0 引脚决定：SA0=0 -> 0x6A，SA0=1 -> 0x6B
const uint8_t ADDR_CANDIDATES[] = {0x6B, 0x6A};

const uint8_t REG_WHO_AM_I  = 0x0F;  // 芯片 ID，应为 0x6B
const uint8_t REG_CTRL1_XL  = 0x10;  // 加速度计控制：采样率 + 量程
const uint8_t REG_CTRL2_G   = 0x11;  // 陀螺仪控制：采样率 + 量程
const uint8_t REG_CTRL3_C   = 0x12;  // BDU + 地址自动递增
const uint8_t REG_STATUS    = 0x1E;  // 数据就绪标志
const uint8_t REG_OUTX_L_G  = 0x22;  // 陀螺仪 X 低字节（0x22~0x27）

// 量程灵敏度：±2g -> 16384 LSB/g；±250dps -> 0.00875 dps/LSB
const float ACCEL_SCALE = 1.0f / 16384.0f;
const float GYRO_SCALE  = 0.00875f;

void writeReg(uint8_t addr, uint8_t reg, uint8_t val) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
}

uint8_t readReg(uint8_t addr, uint8_t reg) {
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.endTransmission(false);
    Wire.requestFrom((int)addr, 1);
    if (Wire.available()) {
        return (uint8_t)Wire.read();
    }
    return 0;
}

}  // namespace

int Imu660RB_Init(Imu660RB *imu) {
    if (imu == 0) {
        return 0;
    }

    Wire.begin();           // UNO：SDA = A4，SCL = A5
    Wire.setClock(400000);  // 400 kHz

    imu->addr = 0;
    for (uint8_t i = 0; i < sizeof(ADDR_CANDIDATES); i++) {
        if (readReg(ADDR_CANDIDATES[i], REG_WHO_AM_I) == 0x6B) {
            imu->addr = ADDR_CANDIDATES[i];
            break;
        }
    }

    if (imu->addr == 0) {
        return 0;
    }

    // ODR = 208 Hz；加速度 ±2g，陀螺仪 ±250 dps
    writeReg(imu->addr, REG_CTRL1_XL, 0x50);
    writeReg(imu->addr, REG_CTRL2_G, 0x50);
    writeReg(imu->addr, REG_CTRL3_C, 0x44);
    delay(50);

    imu->ax = imu->ay = imu->az = 0.0f;
    imu->gx = imu->gy = imu->gz = 0.0f;
    return 1;
}

int Imu660RB_Update(Imu660RB *imu) {
    if (imu == 0 || imu->addr == 0) {
        return 0;
    }

    // 等待加速度计(XLDA)与陀螺仪(GDA)数据就绪
    if ((readReg(imu->addr, REG_STATUS) & 0x03) != 0x03) {
        return 0;
    }

    // 0x22~0x2D 连续 12 字节：gyro X/Y/Z + accel X/Y/Z（各 int16 小端）
    Wire.beginTransmission(imu->addr);
    Wire.write(REG_OUTX_L_G);
    Wire.endTransmission(false);
    Wire.requestFrom((int)imu->addr, 12);

    if (Wire.available() < 12) {
        return 0;
    }

    uint8_t buf[12];
    for (int i = 0; i < 12; i++) {
        buf[i] = (uint8_t)Wire.read();
    }

    int16_t gx = (int16_t)((uint16_t)buf[0]  | ((uint16_t)buf[1]  << 8));
    int16_t gy = (int16_t)((uint16_t)buf[2]  | ((uint16_t)buf[3]  << 8));
    int16_t gz = (int16_t)((uint16_t)buf[4]  | ((uint16_t)buf[5]  << 8));
    int16_t ax = (int16_t)((uint16_t)buf[6]  | ((uint16_t)buf[7]  << 8));
    int16_t ay = (int16_t)((uint16_t)buf[8]  | ((uint16_t)buf[9]  << 8));
    int16_t az = (int16_t)((uint16_t)buf[10] | ((uint16_t)buf[11] << 8));

    imu->gx = gx * GYRO_SCALE;
    imu->gy = gy * GYRO_SCALE;
    imu->gz = gz * GYRO_SCALE;
    imu->ax = ax * ACCEL_SCALE;
    imu->ay = ay * ACCEL_SCALE;
    imu->az = az * ACCEL_SCALE;
    return 1;
}
