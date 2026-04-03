# Twitter Monitor 维护日志

## 服务器信息
- IP：<SERVER_IP>（阿里云）
- 用户：admin
- SSH：`ssh -i ~/.ssh/id_aliyun admin@<SERVER_IP>`
- 部署路径：`/var/www/twitter-monitor/`
- 进程管理：Supervisor

## 常用命令

### 重启服务
```bash
ssh -i ~/.ssh/id_aliyun admin@<SERVER_IP> "sudo supervisorctl restart twitter-monitor-web twitter-monitor-main"
```

### 查看服务状态
```bash
ssh -i ~/.ssh/id_aliyun admin@<SERVER_IP> "sudo supervisorctl status"
```

### 查看日志
```bash
ssh -i ~/.ssh/id_aliyun admin@<SERVER_IP> "sudo supervisorctl tail -f twitter-monitor-web"
ssh -i ~/.ssh/id_aliyun admin@<SERVER_IP> "sudo supervisorctl tail -f twitter-monitor-main"
```

### 部署更新
```bash
# 1. 先 scp 到 /tmp（/var/www 目录权限限制，rsync 直接写入会报 Permission denied）
scp -i ~/.ssh/id_aliyun /Users/namipieces/twitter-monitor/web.py \
  /Users/namipieces/twitter-monitor/contract_gen.py \
  admin@<SERVER_IP>:/tmp/

# 2. sudo 移动到部署目录并重启
ssh -i ~/.ssh/id_aliyun admin@<SERVER_IP> \
  "sudo cp /tmp/web.py /tmp/contract_gen.py /var/www/twitter-monitor/ && \
   sudo supervisorctl restart twitter-monitor-web twitter-monitor-main"
```

## 变更记录

### 2026-03-20
- 合同生成器条款参数化：payment_days / shipping_days / shipping_method / warranty_months / penalty_pct / dispute_clause
- 运费区域重构为三模式（国内/国际/自定义）
- Modal 新增条款折叠编辑区
