# encoding=utf-8
'''
  webtrader 的基础类
'''

import importlib
import sys

importlib.reload(sys)

import hashlib
import multiprocessing
import time
from multiprocessing.pool import ThreadPool as Pool

import numpy as np
import pandas as pd
import requests

from vxTrader import logger

_MAX_LIST = 800

_SINA_STOCK_KEYS = [
    "name", "open", "yclose", "lasttrade", "high", "low", "bid", "ask",
    "volume", "amount", "bid1_m", "bid1_p", "bid2_m", "bid2_p", "bid3_m",
    "bid3_p", "bid4_m", "bid4_p", "bid5_m", "bid5_p", "ask1_m", "ask1_p",
    "ask2_m", "ask2_p", "ask3_m", "ask3_p", "ask4_m", "ask4_p", "ask5_m",
    "ask5_p", "date", "time", "status"]

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko',
    'Pragma': 'no-cache',
    'Connection': 'keep-alive',
    'Accept': '*/*',
    'Accept-Encoding': 'gzip,deflate,sdch',
    'Cache-Conrol': 'no-cache',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept-Language': 'zh-CN,zh;q=0.8'
}

_TIMEOUT = 600


class BrokerFactory():
    '''
    创建一个修饰器，注册vxTrader
    @TraderFactory('yjb', '佣金宝', '国金证券')
    class yjbTrader(WebTrader):
        pass

    '''

    instance = {}

    def __init__(self, *brokerIDs):
        # 使用小写作为关键字
        self._brokerIDs = brokerIDs

    def __call__(self, cls):
        for brokerID in self._brokerIDs:
            BrokerFactory.instance[brokerID.lower()] = cls
        return cls


class LoginSession():
    _objects = {}

    def __new__(cls, account, password):
        '''
        创建loginSession类时，如果同一券商的账号密码都一样时，只创建一次
        '''

        logger.debug('LoginType: %s, account: %s, password: %s' % (type(cls), account, password))

        # cls, account, password 是用MD5进行创建关键字
        m = hashlib.md5()
        m.update(str(type(cls)).encode('utf-8'))
        m.update(account.encode('utf-8'))
        m.update(password.encode('utf-8'))
        keyword = m.hexdigest()

        obj = cls._objects.get(keyword, None)
        logger.debug('keyword: %s, obj: %s' % (keyword, obj))
        if obj is None:
            # 如果没有缓存过此对象，就创建，并进行缓存
            logger.debug('缓存内没有对象，重新创建一个对象')
            obj = super(LoginSession, cls).__new__(cls)
            cls._objects[keyword] = obj

        return obj

    def __init__(self, account, password):

        self._account = account
        self._password = password

        # 内部的session 初始化，expire_at 初始化
        self._session = None
        self._expire_at = 0

        # 初始化线程锁
        self.lock = multiprocessing.Lock()

    def __enter__(self):
        with self.lock:
            now = time.time()
            if now > self._expire_at:
                # 如果登录超时了，重新登录
                # 登录前准备工作
                self.pre_login()
                # 登录
                self.login()
                # 更新超时时间
                self._expire_at = now + _TIMEOUT
                # 执行登陆后初始化工作
                self.post_login()

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def __getattr__(self, name):
        if self._session:
            return self._session.__getattribute__(name)

    def pre_login(self):
        '''
        登录前准备动作，如：创建self._session
        :return:
        '''
        # 默认创建一个requests.session对象
        self._session = requests.session()

    def post_login(self):
        pass

    @property
    def session(self):
        '''
        自动登录，并返回session
        '''
        with self.lock:
            now = time.time()
            if now > self._expire_at:
                # 如果登录超时了，重新登录
                # 登录前准备工作
                self.pre_login()
                # 登录
                self.login()
                # 更新超时时间
                self._expire_at = now + _TIMEOUT
        return self._session

    def login(self):
        '''
        登录接口
        '''
        raise NotImplementedError('Login method is not implemented.')

    def logout(self):
        '''
        退出登录接口
        '''

        self._session = None
        self._expire_at = 0

    def reset(self):
        '''
        重置session
        '''
        self.logout()
        time.sleep(0.5)
        self.session

    def request(self, method, url, **kwargs):
        '''
        调用session的各类http方法
        '''
        logger.debug('Call params: %s' % kwargs)
        with self:
            resq = self.session.request(method=method, url=url, **kwargs)
            resq.raise_for_status()
            logger.debug('return: %s' % resq.text)
            self._expire_at = time.time() + _TIMEOUT
        return resq

    def get(self, url, **kwargs):
        return self.request(method='GET', url=url, **kwargs)

    def post(self, url, **kwargs):
        return self.request(method='POST', url=url, **kwargs)


class WebTrader():
    def __init__(self, account, password, **kwargs):
        self._account = account
        self._password = password

        self._exchange_stock_account = None
        # 初始化线程池
        pool_size = kwargs.pop('pool_size', 5)
        self._worker = Pool(pool_size)
        # session超时时间
        self.expire_at = 0

    def keepalive(self, now=0):
        '''
        自动保持连接的函数
        '''
        if now == 0:
            now = time.time()

        logger.debug('keepalive checking. now: %s, expire_at: %s' % (now, self.expire_at))
        if now + 60 > self.expire_at:
            self.portfolio
            self.expire_at = now + 600
            logger.info('Reflash the expire time, expire_at timestamp is: %s' % self.expire_at)

        return

    @property
    def exchange_stock_account(self):
        '''
        交易所交易账号
        '''
        raise NotImplementedError('login_info method is not implemented.')

    @property
    def portfolio(self):
        '''
        portfolio is a dataframe:
        symbol  : symbol_name, current_amount, enable_amount, lasttrade, market_value, weight
        sz150023  深成指B     1000    500      0.434  4340     0.5
        cash      现金        4340    300      1      4340     0.5
        '''

        pass

    def hq(self, symbols):
        '''
        行情接口——默认使用新浪的行情接口
        :param symbols: [ 'sz150023','sz150022','sz159915']
        :return: 行情数据
        '''
        if symbols is None:
            raise AttributeError('symbols is empty')
        elif isinstance(symbols, str) is True:
            symbols = [symbols]

        url = 'http://hq.sinajs.cn/?rn=%d&list=' % int(time.time())

        urls = [url + ','.join(symbols[i:i + _MAX_LIST]) \
                for i in range(0, len(symbols), _MAX_LIST)]

        respones = self._worker.imap(requests.get, urls)
        data = list()
        for r in respones:
            lines = r.text.splitlines()
            for line in lines:
                d = line.split('"')[1].split(',')
                # 如果格式不正确,则返回nan
                if len(d) != len(_SINA_STOCK_KEYS):
                    d = [np.nan] * len(_SINA_STOCK_KEYS)
                data.append(d)
        df = pd.DataFrame(data, index=symbols, columns=_SINA_STOCK_KEYS, dtype='float')
        df.index.name = 'symbol'
        df.sort_index()
        if 'volume' in _SINA_STOCK_KEYS and 'lasttrade' in _SINA_STOCK_KEYS and 'yclose' in _SINA_STOCK_KEYS:
            df.loc[df.volume == 0, 'lasttrade'] = df['yclose']
        return df

    def buy(self, symbol, price=0, amount=0, volume=0):
        '''
        买入股票
        :return:  order_no
        '''
        raise NotImplementedError('Buy Not Implemented.')

    def sell(self, symbol, price=0, amount=0, volume=0):
        '''
        卖出股票
        :return:  order_no
        '''
        raise NotImplementedError('Sell Not Implemented.')

    def subscribe(self, symbol, volume):
        '''
        场内基金申购接口
        :param symbol: 基金代码,以of 开头
        :param volume: 申购金额
        :return : order_no
        '''
        raise NotImplementedError('Subscription Not Implemented.')

    def redemption(self, symbol, amount):
        '''
        场内基金赎回接口
        :param symbol: 基金代码,以of 开头
        :param amount: 赎回份额
        :return: order_no
        '''
        raise NotImplementedError('Redemption Not Implemented.')

    def split(self, symbol, amount):
        '''
        分级基金分拆接口
        :param symbol: 基金代码,以of 开头
        :param amount: 母基金分拆份额
        :return: order_no
        '''
        raise NotImplementedError('Split Not Implemented.')

    def merge(self, symbol, amount):
        '''
        分级基金合并接口
        :param symbol: 基金代码,以of 开头
        :param amount: 母基金合并份额
        :return: order_no
        '''
        raise NotImplementedError('Merge Not Implemented.')

    @property
    def orderlist(self):
        '''
        获取当日委托列表
        :return: DataFrame
        index : order_no
        columns : symbol, symbol_name, trade_side, order_price, order_amount, business_price, business_amount, order_status, order_time
        '''

        raise NotImplementedError('OrderList Not Implemented.')

    def cancel(self, order_no):
        '''
        撤销下单
        :param order_no:
        :return: order_no
        '''
        raise NotImplementedError('Cancel Not Implemented.')

    def ipo_limit(self):
        '''
        查询当前ipo 认购限额
        :return:
        '''
        raise NotImplementedError('ipo_limit Not Implemented.')

    def ipo_list(self):
        '''
        查询今天IPO股票
        返回列表：
        index: symbol
        columns: symbol_name, exchange_type, subscribe_type, max_buy_amount, buy_unit, money_type, ipo_price, ipo_date, ipo_status
        '''
        raise NotImplementedError('ipo_list Not Implemented.')

    def trans_in(self, cash_in, bank_no=None):
        '''
        资金转入
        :param cash_in:
        :param bank_no:
        :return:
        '''
        raise NotImplementedError('trans_in Not Implemented.')

    def trans_out(self, cash_out, bank_no=None):
        '''
        资金转出
        :param cash_out:
        :param bank_no:
        :return:
        '''
        raise NotImplementedError('trans_out Not Implemented.')
