#!/bin/env python3 
# coding:utf-8
"""
ljk 20161116(update 20170217)
This script should be put in crontab in every web server.Execute every 10 minutes.
Collect nginx access log, process it and insert the result into mysql in 1.21.
"""
import os
import re
import subprocess
import time
import warnings
import pymysql
from sys import argv
from socket import gethostname
from urllib.parse import unquote
from sys import exit
from zlib import crc32
from multiprocessing import Pool


# 定义日志格式，利用非贪婪匹配和分组匹配，需要严格参照日志定义中的分隔符和双引号（编写正则时，先不要换行，确保空格或引号等与日志格式一致，最后考虑美观可以换行）
log_pattern = r'^(?P<remote_addr>.*?) - \[(?P<time_local>.*?)\] "(?P<request>.*?)"' \
              r' (?P<status>.*?) (?P<body_bytes_sent>.*?) (?P<request_time>.*?)' \
              r' "(?P<http_referer>.*?)" "(?P<http_user_agent>.*?)" - (?P<http_x_forwarded_for>.*)$'
log_pattern_obj = re.compile(log_pattern)

# 日志目录和需要处理的站点
log_dir = '/zz_data/nginx_log/'
todo = ['www', 'user']
# exclude_ip = ['192.168.1.200', '192.168.1.202']
# 主机名
global server
server = gethostname()
# 今天零点
global today_start
today_start = time.strftime('%Y-%m-%d', time.localtime()) + ' 00:00:00'
# 将pymysql对于操作中的警告信息转为可捕捉的异常
warnings.filterwarnings('error', category=pymysql.err.Warning)


def my_connect():
    """链接数据库函数"""
    global connection, con_cur
    try:
        connection = pymysql.connect(host='x.x.x.x', user='xxxx', password='xxxx',
                                     charset='utf8mb4', port=3307, autocommit=True, database='log_analyse')
    except pymysql.err.MySQLError as err:
        print('Error: ' + str(err))
        exit(20)
    con_cur = connection.cursor()


def create_table(t_name):
    """创建表函数"""
    my_connect()
    try:
        # url_digest char(32) NOT NULL DEFAULT '' COMMENT '对原始的不含参数的url计算MD5'
        # KEY url_digest (url_digest(8))
        con_cur.execute(
            "CREATE TABLE IF NOT EXISTS {} (\
                id bigint unsigned NOT NULL AUTO_INCREMENT PRIMARY KEY,\
                server char(11) NOT NULL DEFAULT '',\
                url varchar(255) NOT NULL DEFAULT '' COMMENT '去掉参数的url,已做urldecode',\
                url_crc32 bigint unsigned NOT NULL DEFAULT '0' COMMENT '对上面url字段计算crc32',\
                time_local timestamp NOT NULL DEFAULT '0000-00-00 00:00:00',\
                response_code smallint NOT NULL DEFAULT '0',\
                bytes int NOT NULL DEFAULT '0',\
                request_time float(6,3) NOT NULL DEFAULT '0.000',\
                user_ip varchar(40) NOT NULL DEFAULT '',\
                cdn_ip varchar(15) NOT NULL DEFAULT '' COMMENT 'CDN最后节点的ip:空子串表示没经过CDN; - 表示没经过CDN和F5',\
                if_normal tinyint NOT NULL DEFAULT '0' \
                    COMMENT '0(正则根本无法匹配该行日志或日志中$request内容异常) 1(url和arg均正常) 2(url不正常) 3(参数不正常:通过大小判断,200bytes) 4(url和参数都不正常))',\
                KEY time_local (time_local),\
                KEY url_crc32 (url_crc32)\
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4".format(t_name))
    except pymysql.err.Warning:
        pass


def process_line(line_str):
    """
    处理每一行记录
    line_str: 该行数据的字符串形式
    """
    processed = log_pattern_obj.search(line_str)
    if not processed:
        print("Can't match th regex: {}".format(line_str))
        return server, '', 0, '', '', '', '', '', '', 0
    else:
        '''
        # 过滤F5的探测请求,返回None
        for ip in exclude_ip:
            if ip in step1[0]:
                return server, '', '', '', '', '', '', ip, '-', ''
        '''
        # remote_addr (客户若不经过代理，则可认为用户的真实ip)
        remote_addr = processed.group('remote_addr')

        # time_local
        time_local = processed.group('time_local')
        # 转换时间为mysql date类型
        ori_time = time.strptime(time_local.split()[0], '%d/%b/%Y:%H:%M:%S')
        new_time = time.strftime('%Y-%m-%d %H:%M:%S', ori_time)

        # 处理url和参数
        request = processed.group('request')
        request_further = re.split(r'[\s]+', request)
        if len(request_further) == 3:
            '''正常，$request的值应该以空格分为三部分 method full_url schema。有的异常记录可能会少某个字段'''
            full_url = request_further[1]
            url_arg = full_url.split('?', 1)
            # 对日志中经过url_encode过的字符进行还原
            url = unquote(url_arg[0])
            if len(url_arg) == 1:
                arg = ''
            else:
                arg = unquote(url_arg[1])

            # 判断url及arg是否正常
            # if_normal: 1(正常) 2(url不正常,通过大小暂定200b) 3(arg不正常,同过大小暂定200b;or '?' in arg) 4(url和arg都不正常)
            if len(url) > 200:
                if_normal = 2
                if len(arg) > 200 or '?' in arg:
                    if_normal = 4
            else:
                if_normal = 1
                if len(arg) > 200 or '?' in arg:
                    if_normal = 3
            # 计算url MD5(日志里的原始记录,可能是经过url_encode的,区别于写进数据库的url)
            # tmp = hashlib.md5()
            # tmp.update(url.encode())
            # md5 = tmp.hexdigest()
            # 下面的方式耗费了原代码执行时间的95%以上
            # md5 = subprocess.run('echo "{}"|md5sum'.format(url_arg[0]), shell=True, stdout=subprocess.PIPE,
            #                     universal_newlines=True).stdout.split()[0]
            # 对库里的url字段进行crc32校验
            url_crc32 = crc32(url.encode())
        else:
            '''$request不能被正确的被空格分为三段时，正常是可以的'''
            print('$request abnormal: {}'.format(line_str))
            url = request
            # md5 = ''
            url_crc32 = ''
            if_normal = 0

        # 状态码,字节数,响应时间
        response_code = processed.group('status')
        size = processed.group('body_bytes_sent')
        request_time = processed.group('request_time')

        # user_ip,cdn最后节点ip,以及是否经过F5
        http_x_forwarded_for = processed.group('http_x_forwarded_for')
        ips = http_x_forwarded_for.split()
        # user_ip：用户真实ip
        # cdn_ip: CDN最后节点的ip，''表示没经过CDN；'-'表示没经过CDN和F5
        if http_x_forwarded_for == '-':
            '''没经过CDN和F5'''
            user_ip = remote_addr
            cdn_ip = '-'
        elif ips[0] == remote_addr:
            '''没经过CDN，经过F5'''
            user_ip = remote_addr
            cdn_ip = ''
        else:
            '''经过CDN和F5'''
            user_ip = ips[0].rstrip(',')
            cdn_ip = ips[-1]

        return server, url, url_crc32, new_time, response_code, size, request_time, user_ip, cdn_ip, if_normal


def insert_data(line_data, cursor, results, limit, t_name, l_name):
    """
    记录处理之后的数据,累积limit条执行一次插入
    line_data:每行处理之前的字符串数据; 
    limit:每limit行执行一次数据插入; 
    t_name:对应的表名;
    l_name:日志文件名
    """
    line_result = process_line(line_data)

    results.append(line_result)
    # print('len(result):{}'.format(len(result)))    #debug
    if len(results) == limit:
        insert_correct(cursor, results, t_name, l_name)
        results.clear()
        print('{} {} 处理至 {}'.format(time.strftime('%H:%M:%S', time.localtime()), l_name, line_result[3]))


def insert_correct(cursor, results, t_name, l_name):
    """在插入数据过程中处理异常"""
    insert_sql = 'insert into {} (server,url,url_crc32,time_local,response_code,bytes,request_time,user_ip,cdn_ip,if_normal) ' \
                 'values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'.format(t_name)
    try:
        cursor.executemany(insert_sql, results)
    except pymysql.err.Warning as err:
        print('\n{}    Warning: {}'.format(l_name, err))
    except pymysql.err.MySQLError as err:
        print('\n{}    Error: {}'.format(l_name, err))
        print('插入数据时出错...\n')
        connection.close()
        exit(10)


def get_prev_num(t_name, l_name):
    """取得今天已入库的行数 t_name:表名 l_name:日志文件名"""
    try:
        con_cur.execute('select min(id) from {0} where time_local=('
                        'select min(time_local) from {0} where time_local>="{1}")'.format(t_name, today_start))
        min_id = con_cur.fetchone()[0]
        if min_id is not None:  # 假如有今天的数据
            con_cur.execute('select max(id) from {}'.format(t_name))
            max_id = con_cur.fetchone()[0]
            con_cur.execute('select count(*) from {} where id>={} and id<={} and server="{}"'.format(t_name, min_id, max_id, server))
            prev_num = con_cur.fetchone()[0]
        else:
            prev_num = 0
        return prev_num
    except pymysql.err.MySQLError as err:
        print('Error: {}'.format(err))
        print('Error:未取得已入库的行数,本次跳过{}\n'.format(l_name))
        return


def del_old_data(t_name, l_name):
    """删除3天前的数据"""
    # 3天前的日期时间
    three_days_ago = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time()-3600*24*3))
    try:
        con_cur.execute('select max(id) from {0} where time_local=('
                        'select max(time_local) from {0} where time_local!="0000-00-00 00:00:00" and time_local<="{1}")'.format(t_name, three_days_ago))
        max_id = con_cur.fetchone()[0]
        if max_id is not None:
            con_cur.execute('delete from {} where id<={}'.format(t_name, max_id))
    except pymysql.err.MySQLError as err:
        print('\n{}    Error: {}'.format(l_name, err))
        print('未能删除表3天前的数据...\n')


def main_loop(log_name):
    """主逻辑 log_name:日志文件名"""
    table_name = log_name.split('.access')[0].replace('.', '_')  # 将域名例如v.api转换成v_api,因为表名中不能包含'.'
    results = []
    # 创建表
    create_table(table_name)

    # 当前日志文件总行数
    num = int(subprocess.run('wc -l {}'.format(log_dir + log_name), shell=True, stdout=subprocess.PIPE, universal_newlines=True).stdout.split()[0])
    print('num: {}'.format(num))  #debug
    # 上一次处理到的行数
    prev_num = get_prev_num(table_name, log_name)
    if prev_num is not None:
        # 根据当前行数和上次处理之后记录的行数对比,来决定本次要处理的行数范围
        i = 0
        with open(log_name) as fp:
            for line in fp:
                i += 1
                if i <= prev_num:
                    continue
                elif prev_num < i <= num:
                    insert_data(line, con_cur, results, 1000, table_name, log_name)
                else:
                    break
        # 插入不足1000行的results
        if len(results) > 0:
            insert_correct(con_cur, results, table_name, log_name)

    del_old_data(table_name, log_name)


if __name__ == "__main__":
    # 检测如果当前已经有该脚本在运行,则退出
    if_run=subprocess.run('ps -ef|grep {}|grep -v grep|grep -v "/bin/sh"|wc -l'.format(argv[0]),shell=True,stdout=subprocess.PIPE).stdout
    if if_run.decode().strip('\n') == '1':
        os.chdir(log_dir)
        logs_list=os.listdir(log_dir)
        logs_list=[i for i in logs_list if 'access' in i  and os.path.isfile(i) and i.split('.access')[0] in todo]
        # 并行
        with Pool(len(logs_list)) as p:
            p.map(main_loop,logs_list)
