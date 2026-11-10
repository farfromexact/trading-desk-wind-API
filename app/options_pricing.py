"""Forward-based pricing. All vols are fractions, vega is per 1 vol point.

American futures: QuantLib BAW, b=0. A one-year synthetic clock with r*T and
sigma*sqrt(T) preserves both integrated discount and variance, including the
intraday fraction of T; no rounding to integer days. No physical probabilities.
"""
from functools import lru_cache
from math import erf, exp, isfinite, log, pi, sqrt
from threading import RLock

import QuantLib as ql

MODEL_VERSION = 'forward-black76-baw-v1'
_lock = RLock()


def cdf(x):
    return (1 + erf(x / sqrt(2))) / 2


def black_price(f, k, t, vol, rate, side):
    intrinsic=max(side*(f-k),0.)
    if t <= 0:
        return intrinsic
    if vol <= 0:
        return exp(-rate*t)*intrinsic
    s=vol*sqrt(t)
    d1=log(f/k)/s+s/2
    d2=d1-s
    return exp(-rate*t)*side*(f*cdf(side*d1)-k*cdf(side*d2))


@lru_cache(maxsize=12000)
def price(f, k, t, vol, rate, side, style='european'):
    if not all(isfinite(x) for x in (f,k,t,vol,rate)) or min(f,k)<=0 or vol<0 or side not in (-1,1):
        raise ValueError('定价输入无效')
    if t<=0:
        return max(side*(f-k),0.)
    european=black_price(f,k,t,vol,rate,side)
    if style=='european' or abs(rate)<1e-10:
        return european
    if style!='american':
        raise ValueError('未知行权方式')
    if vol*sqrt(t)<1e-6:
        return max(european,side*(f-k),0.)
    with _lock, ql.SavedSettings():
        ref=ql.Date(1,1,2000)
        ql.Settings.instance().evaluationDate=ref
        dc=ql.Actual365Fixed()
        curve=ql.YieldTermStructureHandle(ql.FlatForward(ref,rate*t,dc))
        sigma=ql.BlackVolTermStructureHandle(ql.BlackConstantVol(ref,ql.NullCalendar(),vol*sqrt(t),dc))
        process=ql.BlackScholesMertonProcess(ql.QuoteHandle(ql.SimpleQuote(f)),curve,curve,sigma)
        option=ql.VanillaOption(ql.PlainVanillaPayoff(ql.Option.Call if side==1 else ql.Option.Put,k),ql.AmericanExercise(ref,ref+365))
        option.setPricingEngine(ql.BaroneAdesiWhaleyApproximationEngine(process))
        value=option.NPV()
    if not isfinite(value):
        raise ValueError('美式定价未收敛')
    return max(value,european,side*(f-k),0.)


def implied_vol(premium,f,k,t,rate,side,style='european',tolerance=1e-6):
    if premium<=0 or t<=0:
        raise ValueError('无有效权利金或已经到期')
    low,high=.001,3.
    lower=price(f,k,t,low,rate,side,style)
    upper=price(f,k,t,high,rate,side,style)
    if premium<=lower+tolerance or premium>=upper:
        raise ValueError('权利金不在可识别IV区间内')
    for _ in range(40):
        mid=(low+high)/2
        value=price(f,k,t,mid,rate,side,style)
        if abs(value-premium)<tolerance:
            return mid
        if value>premium:
            high=mid
        else:
            low=mid
    return (low+high)/2


def greeks(f,k,t,vol,rate,side,style):
    h=max(f*.0002,.0001)
    p=price(f,k,t,vol,rate,side,style)
    up=price(f+h,k,t,vol,rate,side,style)
    down=price(f-h,k,t,vol,rate,side,style)
    return dict(delta=(up-down)/(2*h),gamma=(up-2*p+down)/(h*h),
                vega=(price(f,k,t,vol+.001,rate,side,style)-price(f,k,t,max(.0001,vol-.001),rate,side,style))*5,
                theta=price(f,k,max(0,t-1/365),vol,rate,side,style)-p)
