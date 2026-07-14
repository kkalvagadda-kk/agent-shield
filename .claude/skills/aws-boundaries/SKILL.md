---
name: aws-boundaries
description: Use when provisioning, launching, or managing any AWS resource — EC2, VPC, Security Groups, RDS, or any infrastructure. Enforces mandatory region, VPC, subnet, credential, and network constraints.
---

# AWS Boundaries and Restrictions

## Overview
Enforces strict boundaries for all AWS operations. Every AWS command must adhere to these constraints — no exceptions.

## Required Configuration

### 1. Authentication
Always use the `kkalyan-aws-key` AWS profile.
- **Profile Name:** `kkalyan-aws-key`
- **Method:** Append `--profile kkalyan-aws-key` to CLI commands, or `export AWS_PROFILE=kkalyan-aws-key` before scripts.
- **DO NOT** use or ask for raw access keys.

### 2. Region
- **Target Region:** `us-west-2`
- All commands MUST include `--region us-west-2`.

### 3. Network Boundaries
- **VPC ID:** `vpc-0c55644ba799825a2`
- **Subnet IDs:**
  - `subnet-0577cf9c1aac2daf8`
  - `subnet-0493886b7b6086e65`
  - `subnet-0c65769c38fc79822`

**VPN Peering & Connectivity Rules:**
- Laptop VPN is peered directly with the AWS VPC.
- **NO Public IPs** — never use `--associate-public-ip-address`.
- **NO Internet Gateways** — not needed for SSH/SCP access.
- **Always use Private IP** (`PrivateIpAddress`) for SSH/SCP, never Public IP.
- **SSH CIDR:** Restrict port 22 to `10.0.0.0/8`, `172.16.0.0/12`, or `192.168.0.0/16`. **Never** `0.0.0.0/0`.

### 4. Key Pair
- **Key Pair Name:** `kkalyan-finetune-key` — attach to all EC2 instances requiring SSH.

## Execution Checklist

Before any AWS command, verify:
- [ ] Region is `us-west-2`
- [ ] Profile is `kkalyan-aws-key`
- [ ] VPC is `vpc-0c55644ba799825a2`
- [ ] Subnet is one of the three approved subnets
- [ ] NO public IPs assigned
- [ ] SSH/SCP uses Private IP
- [ ] Security Group ingress restricted to internal CIDRs (not `0.0.0.0/0`)
- [ ] EC2 instances use `kkalyan-finetune-key` key pair

## Examples

**Launch EC2 Instance:**
```bash
export AWS_PROFILE=kkalyan-aws-key

aws ec2 run-instances \
    --image-id ami-0c55b159cbfafe1f0 \
    --count 1 \
    --instance-type t2.micro \
    --key-name kkalyan-finetune-key \
    --subnet-id subnet-0577cf9c1aac2daf8 \
    --region us-west-2
```

**Create Security Group Rule:**
```bash
aws ec2 authorize-security-group-ingress \
    --group-id sg-0123456789abcdef0 \
    --protocol tcp \
    --port 22 \
    --cidr 10.0.0.0/8 \
    --region us-west-2 \
    --profile kkalyan-aws-key
```
