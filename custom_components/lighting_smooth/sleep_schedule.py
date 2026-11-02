"""Daily local-time sleep windows, including intervals across midnight."""
from datetime import datetime,time,timedelta


def clock(value):
    if isinstance(value,time):
        result=value
    elif isinstance(value,str):
        result=time.fromisoformat(value)
    else:
        raise ValueError("请输入有效的睡眠时间")
    if result.tzinfo is not None:
        raise ValueError("睡眠时间应使用 HA 本地时间")
    return result.replace(microsecond=0)


def sleep_window(now,start,end):
    start,end=clock(start),clock(end)
    if start==end:
        raise ValueError("睡眠开始和结束时间不能相同")
    start_at=datetime.combine(now.date(),start,tzinfo=now.tzinfo)
    end_at=datetime.combine(now.date(),end,tzinfo=now.tzinfo)
    if start>end:
        if now<end_at:
            start_at-=timedelta(days=1)
        else:
            end_at+=timedelta(days=1)
    if start_at<=now<end_at:
        return start_at,end_at
    return None
