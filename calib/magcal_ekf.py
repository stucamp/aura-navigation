#!/usr/bin/python

import argparse
import fileinput
import geomag
from mpl_toolkits.mplot3d import Axes3D
import matplotlib.pyplot as plt
import math
import numpy as np
import os
import re
from scipy import interpolate
import sys

import navpy

from nav.data import flight_data, flight_interp, imucal

import transformations

parser = argparse.ArgumentParser(description='magcal')
parser.add_argument('--flight', help='load specified aura flight log')
parser.add_argument('--aura-flight', help='load specified aura flight log')
parser.add_argument('--px4-sdlog2', help='load specified px4 sdlog2 (csv) flight log')
parser.add_argument('--px4-ulog', help='load specified px4 ulog (csv) base path')
parser.add_argument('--umn-flight', help='load specified .mat flight log')
parser.add_argument('--sentera-flight', help='load specified sentera flight log')
parser.add_argument('--sentera2-flight', help='load specified sentera2 flight log')
parser.add_argument('--cal', required=True, help='calibration log directory')
parser.add_argument('--imu-sn', help='specify imu serial number')
parser.add_argument('--resample-hz', type=float, default=10.0, help='resample rate (hz)')
parser.add_argument('--xmin', type=float, help='start time')
parser.add_argument('--xmax', type=float, help='end time')
parser.add_argument('--plot', action='store_true', help='plot results.')
args = parser.parse_args()

g = 9.81
r2d = 180.0 / math.pi

if args.flight:
    loader = 'aura'
    path = args.flight
elif args.aura_flight:
    loader = 'aura'
    path = args.aura_flight
elif args.px4_sdlog2:
    loader = 'px4_sdlog2'
    path = args.px4_sdlog2
elif args.px4_ulog:
    loader = 'px4_ulog'
    path = args.px4_ulog
elif args.sentera_flight:
    loader = 'sentera1'
    path = args.sentera_flight
elif args.sentera2_flight:
    loader = 'sentera2'
    path = args.sentera2_flight
elif args.umn_flight:
    loader = 'umn1'
    path = args.umn_flight
else:
    loader = None
    path = None
if 'recalibrate' in args:
    recal_file = args.recalibrate
else:
    recal_file = None

data = flight_data.load(loader, path, recal_file)
interp = flight_interp.FlightInterpolate()
interp.build(data)

print "imu records:", len(data['imu'])
print "gps records:", len(data['gps'])
if 'air' in data:
    print "airdata records:", len(data['air'])
print "filter records:", len(data['filter'])
if 'pilot' in data:
    print "pilot records:", len(data['pilot'])
if 'act' in data:
    print "act records:", len(data['act'])
if len(data['imu']) == 0:
    print "not enough data loaded to continue."
    quit()

# imu_data = np.array(imu_data, dtype=np.float64)
# x = imu_data[:,0]
# imu_hx = interpolate.interp1d(x, imu_data[:,7], bounds_error=False, fill_value=0.0)
# imu_hy = interpolate.interp1d(x, imu_data[:,8], bounds_error=False, fill_value=0.0)
# imu_hz = interpolate.interp1d(x, imu_data[:,9], bounds_error=False, fill_value=0.0)

# filter_data = np.array(filter_data, dtype=float)
# x = filter_data[:,0]
# filter_alt = interpolate.interp1d(x, filter_data[:,3])
# filter_phi = interpolate.interp1d(x, filter_data[:,7])
# filter_the = interpolate.interp1d(x, filter_data[:,8])
# filter_psi = interpolate.interp1d(x, filter_data[:,9])
alt_min = alt_max = data['filter'][0].alt
for f in data['filter']:
    if f.alt < alt_min: alt_min = f.alt
    if f.alt > alt_max: alt_max = f.alt
alt_cutoff = alt_min + (alt_max - alt_min) * 0.25
print "Alt range =", alt_min, alt_max, "cutoff =", alt_cutoff

# determine ideal magnetometer in ned coordinates
base_lat = data['filter'][0].lat * r2d
base_lon = data['filter'][0].lon * r2d
print "starting at:", base_lat, base_lon
gm = geomag.geomag.GeoMag("/usr/lib/python2.7/site-packages/geomag/WMM.COF")
mag = gm.GeoMag(base_lat, base_lon)
mag_ned = np.array( [mag.bx, mag.by, mag.bz] )
print 'raw mag vector:', mag_ned
norm = np.linalg.norm(mag_ned)
print '          norm:', norm
mag_ned /= norm
print '    normalized:', mag_ned

# read the events.txt file to determine when aircraft becomes airborne
# (so we can ignore preflight values.)  Update: also to read the IMU
# serial number.
xmin = None
xmax = None
imu_sn = None
if args.flight:
    events_file = os.path.join(args.flight, 'events.txt')
    fevents = fileinput.input(events_file)
    for line in fevents:
        tokens = line.split()
        if len(tokens) == 3 and tokens[2] == 'airborne' and not xmin:
            xmin = float(tokens[0])
            print "airborne (launch) at t =", xmin
        elif len(tokens) == 5 and tokens[3] == 'complete:' and tokens[4] == 'launch' and not xmax:
            # haven't found a max yet, so update min
            xmin = float(tokens[0])
            print "flight begins at t =", xmin                    
        elif len(tokens) == 4 and float(tokens[0]) > 0 and tokens[2] == 'on' and tokens[3] == 'ground' and not xmax:
            t = float(tokens[0])
            if t - xmin > 60:
                xmax = float(tokens[0])
                print "flight complete at t =", xmax
            else:
                print "warning ignoring sub 1 minute hop"
        elif len(tokens) == 6 and tokens[1] == 'APM2:' and tokens[2] == 'Serial' and tokens[3] == 'Number':
            imu_sn = 'apm2_' + tokens[5]
        elif len(tokens) == 5 and tokens[1] == 'APM2' and tokens[2] == 'Serial' and tokens[3] == 'Number:':
            imu_sn = 'apm2_' + tokens[4]
    if imu_sn:
        print 'IMU s/n:', imu_sn
    else:
        print 'Cannot determine IMU serial number from events.txt file'

if args.imu_sn:
    imu_sn = args.imu_sn
    print 'Using serial number from command line:', imu_sn
    
if not imu_sn:
    print 'Cannot continue without an IMU serial number'
    quit()

if args.xmin:
    xmin = args.xmin
    print 'xmin provided:', xmin
if not xmin:
    print "warning no launch event found"
    xmin = interp.imu_time.min()
    
if args.xmax:
    xmax = args.xmax
    print 'xmax provided:', xmax
if not xmax:
    print "warning no land event found"
    xmax = interp.imu_time.max()

# sanity check in case imu data log ends before events.txt
if xmin < interp.imu_time.min():
    xmin = interp.imu_time.min()
if xmax > interp.imu_time.max():
    xmax = interp.imu_time.max()
    
print "flight range = %.3f - %.3f (%.3f)" % (xmin, xmax, xmax-xmin)
trange = xmax - xmin

sense_data = []
ideal_data = []

for i, x in enumerate( np.linspace(xmin, xmax, trange*args.resample_hz) ):
    alt = interp.filter_alt(x)
    phi = interp.filter_phi(x)
    the = interp.filter_the(x)
    psix = interp.filter_psix(x)
    psiy = interp.filter_psiy(x)
    psi = math.atan2(psiy, psix)
    # print phi, the, psi
    N2B = navpy.angle2dcm(psi, the, phi, input_unit='rad')
    mag_ideal = N2B.dot(mag_ned)
    norm = np.linalg.norm(mag_ideal)
    mag_ideal /= norm
    hx = interp.imu_hx(x)
    hy = interp.imu_hy(x)
    hz = interp.imu_hz(x)
    if abs(hx) > 1000:
        print "oops:", hx, hy, hz
    mag_sense = np.array([hx, hy, hz])
    # if abs(psi) < 0.1:
    if args.flight:
        ideal_data.append( mag_ideal[:].tolist() )
        sense_data.append( mag_sense[:].tolist() )
    elif alt >= alt_cutoff:
        print mag_sense, mag_ideal
        ideal_data.append( mag_ideal[:].tolist() )
        sense_data.append( mag_sense[:].tolist() )

ideal_array = np.array(ideal_data, dtype=np.float64)
sense_array = np.array(sense_data, dtype=np.float64)

# compute affine transformation between sensed data and ideal data,
# this is our best estimate of the ideal magnetometer calibration
# using the EKF inertial only solution.

affine = transformations.affine_matrix_from_points(sense_array.T, ideal_array.T)
print "affine:"
np.set_printoptions(precision=10,suppress=True)
print affine
scale, shear, angles, translate, perspective = transformations.decompose_matrix(affine)
print ' scale:', scale
print ' shear:', shear
print ' angles:', angles
print ' trans:', translate
print ' persp:', perspective

# write calibration data points to file (so we can aggregate over
# multiple flights later
if args.flight:
    data_dir = os.path.abspath(args.flight)
elif args.sentera_flight:
    data_dir = os.path.abspath(args.sentera)
elif args.px4_sdlog2:
    data_dir = os.path.dirname(os.path.abspath(args.px4_sdlog2))

cal_dir = os.path.join(args.cal, imu_sn)
if not os.path.exists(cal_dir):
    os.makedirs(cal_dir)
filename = os.path.basename(data_dir) + "-mags.txt"
mags_file = os.path.join(cal_dir, filename)
print "mags file:", mags_file
f = open(mags_file, 'w')
for i in range(sense_array.shape[0]):
    f.write( "%.4f %.4f %.4f %.4f %.4f %.4f\n" %
             (sense_array[i][0], sense_array[i][1], sense_array[i][2],
              ideal_array[i][0], ideal_array[i][1], ideal_array[i][2]))
f.close()

# generate affine mapping
af_data = []
for i, s in enumerate(sense_array):
    hs = np.hstack( [s, 1.0] )
    af = np.dot(affine, hs)
    norm = np.linalg.norm(af[:3])
    #print 'affine mapped vector norm:', norm
    #print 'ideal:', ideal_array[i], 'af:', af[:3]
    af[:3] /= norm
    af_data.append(af[:3])
af_array = np.array(af_data)


if args.plot:
    cal_fig, cal_mag = plt.subplots(3, sharex=True)
    cal_mag[0].plot(sense_array[:,0],ideal_array[:,0],'r.',alpha=0.5,label='EKF Estimate')
    cal_mag[0].plot(sense_array[:,0],af_array[:,0],'g.',alpha=0.5,label='Affine Cal')
    cal_mag[0].set_xlabel('(hx) Sensed Mag')
    cal_mag[0].set_ylabel('(hx) Ideal Mag Est')
    cal_mag[0].set_title('Magnetometer Calibration')
    cal_mag[0].legend(loc=0)

    cal_mag[1].plot(sense_array[:,1],ideal_array[:,1],'r.',alpha=0.5,label='hy')
    cal_mag[1].plot(sense_array[:,1],af_array[:,1],'g.',alpha=0.5,label='hy')
    cal_mag[1].set_xlabel('(hy) Sensed Mag')
    cal_mag[1].set_ylabel('(hy) Ideal Mag Est')

    cal_mag[2].plot(sense_array[:,2],ideal_array[:,2],'r.',alpha=0.5,label='hz')
    cal_mag[2].plot(sense_array[:,2],af_array[:,2],'g.',alpha=0.5,label='hz')
    cal_mag[2].set_xlabel('(hz) Sensed Mag')
    cal_mag[2].set_ylabel('(hz) Ideal Mag')

    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')
    ax.scatter(ideal_array[:,0], ideal_array[:,1], ideal_array[:,2], c='b',alpha=0.5,label='Ideal Mag (EKF)')
    ax.scatter(af_array[:,0], af_array[:,1], af_array[:,2], c='r',alpha=0.5,label='Calibrated Mag')
    ax.set_xlabel('hx')
    ax.set_ylabel('hy')
    ax.set_zlabel('hz')
    ax.legend(loc=0)
    plt.show()

