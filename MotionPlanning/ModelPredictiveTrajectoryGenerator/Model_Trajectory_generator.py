#! /usr/bin/enc python
# -*- coding: utf-8 -*-
# author: Irving He 
# email: 1910646@tongji.edu.cn

"""
此模块为第二步:
>> 轨迹生成(Trajectory Generation)
>> 此时已经知道全局路径信息
>> 结合局部障碍物信息、交通规则等
>> 生成一系列轨迹并选出最优轨迹

>> 通过Model Predictive来解决BVP问题(BVP: Boundary Value Problem)
>> A --> B(goal)
>> state: x(x,y,theta) 3D
>> action: u(v,k) velocity and curvature(油门刹车 & 方向盘)
---------------------------------------------------------
MPG属于control discretization
-1- 车辆模型:Simple Car Model
-2- 状态约束:
    车辆导航约束(x,y,theta)
    状态目标:(xc,yc,theta_c) --- > goal
-3- 控制参数化:
    车辆控制分为两个部分:
    1) 基于线性速度方程 : v(p,t)
    --- 常见velocity profile: constant profile、linear profile、linear ramp profile ...
    --- 一般来说，速度配置参数是确定的

    2) 基于路程的曲率方程: k(p,s) (s是路程)
    --- second order spline profile 二次函数的曲率配置
    --- 参数[k0,k1,k2,sf] 为了曲率平滑一般k0固定

    综上，自由参数 params[free] = [k1,k2,sf].T

-4- 初始化轨迹 --- 初始和最终状态进行离散，5个维度：
    均匀采样所有可取的控制参数params[free], 根据模型得到相应的轨迹
        1) relative intial and terminal pos (delta_x,delta_y)
        2) relative heading (delta_theta)
        3) initial curvature (k_i) (k0)
        4) constant velocities (v) (+/-)
    初始化根据行为规划中输出的目标未知(x,y,theta)取距离最近的几条对应的控制参数
    (k1,k2,sf) --- 这样初始化的轨迹比较接近最优轨迹，优化速度快;

-5- 轨迹优化 --- Jacobi矩阵
    控制参数p的收敛方向计算, 优化目标使得目标函数C(x,p) --> 0
    C(x,p2) = C(x,p1_) + J(p2-p1) (1阶Taylor)
    优化目标： 目标函数小于某个值或者发散，发散取最接近的一组参数

---------------------------------------------------------
"""

import math

import motion_model as mm

import numpy as np
import matplotlib.pyplot as plt

# optimization parameter
# max iterations
max_iter = 100

# parameter sampling distance
# parameter increment
# 0.5 --- for s
# 0.02 --- for km/kf (middle&final curvature point)
h = np.array([0.5,0.02,0.02]).T

# loss threshold
cost_th = 0.1

# PARAMS
MINCOST = float('inf')
MINA = 1.0  # 参数/ss
MAXA = 2.0  # 参数/ss
DA = 0.5  # acceleration increment

show_animation = True

def plot_arrow(x, y, yaw, length=1.0, width=0.5, fc="r", ec="k"):  # pragma: no cover
    """
    Plot arrow
    """
    plt.arrow(x, y, length * math.cos(yaw), length * math.sin(yaw),
              fc=fc, ec=ec, head_width=width, head_length=width)
    plt.plot(x, y)
    plt.plot(0, 0)

def show_trajectory(target, xc, yc,yawc):  # pragma: no cover
    plt.clf()
    plot_arrow(target.x, target.y, target.yaw)
    plt.plot(xc, yc, "-r")
    plot_arrow(xc[-1],yc[-1],yawc[-1])
    plt.axis("equal")
    plt.grid(True)
    plt.pause(0.1)

def calc_diff(target,x,y,yaw):
    # the difference between the target pose and the last pose
    # for computing the cost
    d = np.array([target.x - x[-1],
                  target.y - y[-1],
                  mm.pi_2_pi(target.yaw-yaw[-1])])
    return d

def clac_j(target,p,h,k0):
    """
    计算Loss(target,current_state)的雅克比矩阵
    :param target: target
    :param p: [s,km,kf]
    :param h: increment
    :param k0: 固定死，只有km,kf是活的
    :return:
    """
    # 计算Jacobi matrix
    # -[d(C(x,p))/d(p)]
    # dp包括 ds, dkm, df

    # p[0,0] ---> s +/- 0.5
    # xp,yp,yawp 是由p[0,0]+h[0]所产生的所有可能的状态
    xp, yp, yawp = mm.generate_last_state(
        p[0, 0] + h[0], p[1, 0], p[2, 0], k0)
    dp = calc_diff(target, [xp], [yp], [yawp])
    xn, yn, yawn = mm.generate_last_state(
        p[0, 0] - h[0], p[1, 0], p[2, 0], k0)
    dn = calc_diff(target, [xn], [yn], [yawn])
    d1 = np.array((dp - dn) / (2.0 * h[0])).reshape(3, 1)

    # p[1,0] ---> km +/- 0.02
    xp, yp, yawp = mm.generate_last_state(
        p[0, 0], p[1, 0] + h[1], p[2, 0], k0)
    dp = calc_diff(target, [xp], [yp], [yawp])
    xn, yn, yawn = mm.generate_last_state(
        p[0, 0], p[1, 0] - h[1], p[2, 0], k0)
    dn = calc_diff(target, [xn], [yn], [yawn])
    d2 = np.array((dp - dn) / (2.0 * h[1])).reshape(3, 1)

    # p[2,0] ---> kf +/- 0.02
    xp, yp, yawp = mm.generate_last_state(
        p[0, 0], p[1, 0], p[2, 0] + h[2], k0)
    dp = calc_diff(target, [xp], [yp], [yawp])
    xn, yn, yawn = mm.generate_last_state(
        p[0, 0], p[1, 0], p[2, 0] - h[2], k0)
    dn = calc_diff(target, [xn], [yn], [yawn])
    d3 = np.array((dp - dn) / (2.0 * h[2])).reshape(3, 1)

    # d(Loss(x,p))/d(p)
    # p including s、km、kf
    J = np.hstack((d1,d2,d3)) # 3x3

    return J

def selection_learning_params(dp,p,k0,target):
    """
    选择学习的参数
    :param dp: params[ds,dkm,dkf] 变化量
    :param p:  params[s,km,kf]
    :param k0: 初始曲率点
    :param target: 目标
    :return:
    """
    mincost = MINCOST
    mina = MINA
    maxa = MAXA
    da = DA

    acc_space = np.arange(mina,maxa,da)

    for a in acc_space:
        # 加速度搜索
        tp = p + a*dp
        xc,yc,yawc = mm.generate_last_state(
            tp[0],tp[1],tp[2],k0
        )
        dc = calc_diff(target,[xc],[yc],[yawc])
        cost = np.linalg.norm(dc)

        if cost <= mincost and a != 0.0:
            mina = a
            mincost = cost

    return mina

def optimization_trajectory(target,k0,p):
    """
    轨迹优化
    target: end point
    k0: initial
    p : [s,km,kf]
    :return: xc,yc,yawc,p
    """
    # 迭代max_iter次
    for i in range(max_iter):
        # xc,yc,yawc, 按照当前运动模型生成的轨迹的集合
        xc,yc,yawc = mm.generate_trajectory(p[0],p[1],p[2],k0)

        # 计算目标点与各个轨迹点的diff
        dc = np.array(calc_diff(target,xc,yc,yawc)).reshape(3,1)

        # 计算cost
        # cost1 = (dx)^2
        # cost2 = (dy)^2
        # cost3 = (dyaw)^2
        cost = np.linalg.norm(dc)

        # 选出符合约束的路径
        # 未考虑其他因素 如comfor、behavior、safety ...
        if cost <= cost_th:
            print("Path is ok, current cost is:" + str(cost))
            break

        # p[s,km,kf]
        J = clac_j(target,p,h,k0)

        try:
            dp = -np.linalg.inv(J) @ dc
        except np.linalg.linalg.LinAlgError:
            print("cannot calc path LinAlgError")
            xc,yc,yawc,p = None,None,None,None
            break

        alpha = selection_learning_params(dp,p,k0,target)

        # Update params
        p += alpha * np.array(dp)

        if show_animation:
            show_trajectory(target,xc,yc,yawc)

    else:
        xc,yc,yawc,p = None,None,None,None
        print("cannot calc path!")

    return xc,yc,yawc,p


def test_optimize_trajectory(): # pragma: no cover

    # Target (preset)
    target = mm.State(
        x = 10.0,
        y = 0.0,
        yaw = np.deg2rad(60.0)
    )

    k0 = 0.0 # initial curvature: 0 1/m

    init_p = np.array([6.0,0.0,np.deg2rad(45)]).reshape(3,1)

    x,y,yaw,p = optimization_trajectory(target,k0,init_p)

    if show_animation:
        show_trajectory(target, x, y,yaw)
        plot_arrow(target.x, target.y, target.yaw)
        plt.axis("equal")
        plt.grid(True)
        plt.show()

    plt.plot(x,y,"r--")
    plt.show()

def main():  # pragma: no cover
    print(__file__ + " start!!")
    test_optimize_trajectory()
#
# if __name__ == '__main__':
#     main()