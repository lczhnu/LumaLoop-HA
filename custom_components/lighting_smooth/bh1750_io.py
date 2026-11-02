"""Linux BH1750 I/O; executor only. Named to avoid HA's hardware platform."""
import os
import time


def read_bh1750(bus=1, address=0x23):
    import fcntl  # Linux only; entity-backed deployments do not import it.
    fd=os.open(f"/dev/i2c-{bus}",os.O_RDWR)
    try:
        fcntl.ioctl(fd,0x0703,address)  # I2C_SLAVE: does not force a busy device
        for command in (0x01,0x07,0x42,0x65,0x20):
            if os.write(fd,bytes([command])) != 1:
                raise OSError("I2C 命令写入不完整")
        time.sleep(0.2)
        data=os.read(fd,2)
        if len(data)!=2:
            raise OSError("I2C 返回长度错误")
        raw=int.from_bytes(data,"big")
        return round(raw/1.2,1),raw
    finally:
        os.close(fd)
