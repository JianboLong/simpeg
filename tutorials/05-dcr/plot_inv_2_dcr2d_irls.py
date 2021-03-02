"""
2.5D DC Resistivity Inversion with Sparse Norms
===============================================

Here we invert a line of DC resistivity data to recover an electrical
conductivity model. We formulate the inverse problem as a least-squares
optimization problem. For this tutorial, we focus on the following:

    - Defining the survey
    - Generating a mesh based on survey geometry
    - Including surface topography
    - Defining the inverse problem (data misfit, regularization, directives)
    - Applying sensitivity weighting
    - Plotting the recovered model and data misfit


"""

#########################################################################
# Import modules
# --------------
#

import os
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
import tarfile

from discretize import TreeMesh
from discretize.utils import mkvc, refine_tree_xyz

from SimPEG.utils import surface2ind_topo
from SimPEG import (
    maps,
    data,
    data_misfit,
    regularization,
    optimization,
    inverse_problem,
    inversion,
    directives,
    utils,
)
from SimPEG.electromagnetics.static import resistivity as dc
from SimPEG.electromagnetics.static.utils.static_utils import (
    plot_2d_pseudosection, apparent_resistivity_from_voltage
)

try:
    from pymatsolver import Pardiso as Solver
except ImportError:
    from SimPEG import SolverLU as Solver

mpl.rcParams.update({'font.size': 16})
# sphinx_gallery_thumbnail_number = 3


#############################################
# Define File Names
# -----------------
#
# Here we provide the file paths to assets we need to run the inversion. The
# path to the true model conductivity and chargeability models are also
# provided for comparison with the inversion results. These files are stored as a
# tar-file on our google cloud bucket:
# "https://storage.googleapis.com/simpeg/doc-assets/dcip2d.tar.gz"
#

# storage bucket where we have the data
# data_source = "https://storage.googleapis.com/simpeg/doc-assets/dcip2d.tar.gz"

# download the data
# downloaded_data = utils.download(data_source, overwrite=True)

# unzip the tarfile
# tar = tarfile.open(downloaded_data, "r")
# tar.extractall()
# tar.close()

# path to the directory containing our data
# dir_path = downloaded_data.split(".")[0] + os.path.sep


dir_path = os.path.dirname(dc.__file__).split(os.path.sep)[:-4]
dir_path.extend(["tutorials", "05-dcr", "dcr2d"])
dir_path = os.path.sep.join(dir_path) + os.path.sep

# files to work with
topo_filename = dir_path + "xyz_topo.txt"
data_filename = dir_path + "dc_data.obs"
true_conductivity_filename = dir_path + "true_conductivity.txt"


#############################################
# Load Data, Define Survey and Plot
# ---------------------------------
#
# Here we load the observed data, define the DC and IP survey geometry and
# plot the data values using pseudo-sections.
# **Warning**: In the following example, the observations file is assumed to be
# sorted by sources
#

# Load data
topo_xyz = np.loadtxt(str(topo_filename))
dobs = np.loadtxt(str(data_filename))

# Extract source and receiver electrode locations and the observed data
A_electrodes = dobs[:, 0:2]
B_electrodes = dobs[:, 2:4]
M_electrodes = dobs[:, 4:6]
N_electrodes = dobs[:, 6:8]
dobs = dobs[:, -1]

# Define survey
unique_tx, k = np.unique(np.c_[A_electrodes, B_electrodes], axis=0, return_index=True)
n_sources = len(k)
k = np.r_[k, len(A_electrodes) + 1]

source_list = []
for ii in range(0, n_sources):

    # MN electrode locations for receivers. Each is an (N, 3) numpy array
    M_locations = M_electrodes[k[ii] : k[ii + 1], :]
    N_locations = N_electrodes[k[ii] : k[ii + 1], :]
    receiver_list = [dc.receivers.Dipole(M_locations, N_locations, data_type="volt")]

    # AB electrode locations for source. Each is a (1, 3) numpy array
    A_location = A_electrodes[k[ii], :]
    B_location = B_electrodes[k[ii], :]
    source_list.append(dc.sources.Dipole(receiver_list, A_location, B_location))

# Define survey
survey = dc.survey.Survey(source_list)

#######################################################################
# Plot Observed Data in Pseudo-Section
# ------------------------------------
#
# Here, we demonstrate how to plot 2D data in pseudo-section.
# First, we plot the actual data (voltages) in pseudo-section as a scatter plot.
# This allows us to visualize the pseudo-sensitivity locations for our survey.
# Next, we plot the data as apparent conductivities in pseudo-section with a filled
# contour plot.
#

# Plot voltages pseudo-section
fig = plt.figure(figsize=(12, 5))
ax1 = fig.add_axes([0.1, 0.15, 0.75, 0.78])
plot_2d_pseudosection(
    survey,
    np.abs(dobs),
    'scatter',
    ax=ax1,
    scale="log",
    units="V/A",
    scatter_opts={"cmap": mpl.cm.viridis},
)
ax1.set_title("Normalized Voltages")
plt.show()

# Get apparent conductivities from volts and survey geometry
apparent_conductivities = 1/apparent_resistivity_from_voltage(survey, dobs)

# Plot apparent conductivity pseudo-section
fig = plt.figure(figsize=(12, 5))
ax1 = fig.add_axes([0.1, 0.15, 0.75, 0.78])
plot_2d_pseudosection(
    survey,
    apparent_conductivities,
    'tricontourf',
    ax=ax1,
    scale="log",
    units="S/m",
    mask_topography=True,
    tricontourf_opts={"levels": 20, "cmap": mpl.cm.viridis},
)
ax1.set_title("Apparent Conductivity")
plt.show()

#############################################
# Define Data and Assign Uncertainties
# ------------------------------------
#
# Inversion with SimPEG requires that we define standard deviation on our data.
# This represents our estimate of the noise in our data. For DC data, a relative
# error is applied to each datum.
#

# Define a data object
dc_data = data.Data(survey, dobs=dobs)

# Compute standard deviations
std = 0.05 * np.abs(dobs)

# Add standard deviations to data object
dc_data.standard_deviation = std

########################################################
# Create Tree Mesh
# ------------------
#
# Here, we create the Tree mesh that will be used invert the DC data
#

dh = 8  # base cell width
dom_width_x = 2400.0  # domain width x
dom_width_z = 1200.0  # domain width z
nbcx = 2 ** int(np.round(np.log(dom_width_x / dh) / np.log(2.0)))  # num. base cells x
nbcz = 2 ** int(np.round(np.log(dom_width_z / dh) / np.log(2.0)))  # num. base cells z

# Define the base mesh
hx = [(dh, nbcx)]
hz = [(dh, nbcz)]
mesh = TreeMesh([hx, hz], x0="CN")

# Mesh refinement based on topography
mesh = refine_tree_xyz(
    mesh, topo_xyz[:, [0, 2]], octree_levels=[0, 2], method="surface", finalize=False
)

# Mesh refinement near transmitters and receivers. First we need to obtain the
# set of unique electrode locations.
electrode_locations = np.c_[
    survey.locations_a,
    survey.locations_b,
    survey.locations_m,
    survey.locations_n,
]

unique_locations = np.unique(
    np.reshape(electrode_locations, (4 * survey.nD, 2)), axis=0
)

mesh = refine_tree_xyz(
    mesh, unique_locations, octree_levels=[2, 4], method="radial", finalize=False
)

# Refine core mesh region
xp, zp = np.meshgrid([-800.0, 800.0], [-800.0, 0.0])
xyz = np.c_[mkvc(xp), mkvc(zp)]
mesh = refine_tree_xyz(mesh, xyz, octree_levels=[0, 2, 2], method="box", finalize=False)

mesh.finalize()


###############################################################
# Project Surveys to Discretized Topography
# -----------------------------------------
#
# It is important that electrodes are not model as being in the air. Even if the
# electrodes are properly located along surface topography, they may lie above
# the discretized topography. This step is carried out to ensure all electrodes
# like on the discretized surface.
#

# Create 2D topography. Since our 3D topography only changes in the x direction,
# it is easy to define the 2D topography projected along the survey line. For
# arbitrary topography and for an arbitrary survey orientation, the user must
# define the 2D topography along the survey line.
topo_2d = np.unique(topo_xyz[:, [0, 2]], axis=0)

# Find cells that lie below surface topography
ind_active = surface2ind_topo(mesh, topo_2d)

# Shift electrodes to the surface of discretized topography
survey.drape_electrodes_on_topography(mesh, ind_active, option="top")

########################################################
# Starting/Reference Model and Mapping on Tree Mesh
# ---------------------------------------------------
#
# Here, we would create starting and/or reference models for the DC inversion as
# well as the mapping from the model space to the active cells. Starting and
# reference models can be a constant background value or contain a-priori
# structures. Here, the starting model is the natural log of 0.01 S/m.
#

# Define conductivity model in S/m (or resistivity model in Ohm m)
air_conductivity = np.log(1e-8)
background_conductivity = np.log(1e-2)

active_map = maps.InjectActiveCells(mesh, ind_active, np.exp(air_conductivity))
nC = int(ind_active.sum())

conductivity_map = active_map * maps.ExpMap()

# Define model
starting_conductivity_model = background_conductivity * np.ones(nC)

##############################################
# Define the Physics of the DC Simulation
# ---------------------------------------
#
# Here, we define the physics of the DC resistivity problem.
#

# Define the problem. Define the cells below topography and the mapping
simulation = dc.simulation_2d.Simulation2DNodal(
    mesh, survey=survey, sigmaMap=conductivity_map, Solver=Solver
)

#######################################################################
# Define DC Inverse Problem
# -------------------------
#
# The inverse problem is defined by 3 things:
#
#     1) Data Misfit: a measure of how well our recovered model explains the field data
#     2) Regularization: constraints placed on the recovered model and a priori information
#     3) Optimization: the numerical approach used to solve the inverse problem
#
#

# Define the data misfit. Here the data misfit is the L2 norm of the weighted
# residual between the observed data and the data predicted for a given model.
# Within the data misfit, the residual between predicted and observed data are
# normalized by the data's standard deviation.
dmis = data_misfit.L2DataMisfit(data=dc_data, simulation=simulation)

# Define the regularization (model objective function). Here, 'p' defines the
# the norm of the smallness term, 'qx' defines the norm of the smoothness
# in x and 'qz' defines the norm of the smoothness in z.
regmap = maps.IdentityMap(nP=int(ind_active.sum()))

reg = regularization.Sparse(
    mesh,
    indActive=ind_active,
    mref=starting_conductivity_model,
    mapping=regmap,
    gradientType="components",
    alpha_s=0.01,
    alpha_x=1,
    alpha_y=1,
)

p = 0
qx = 1
qz = 1
reg.norms = np.c_[p, qx, qz]

# Define how the optimization problem is solved. Here we will use an inexact
# Gauss-Newton approach.
opt = optimization.InexactGaussNewton(maxIter=40)

# Here we define the inverse problem that is to be solved
inv_prob = inverse_problem.BaseInvProblem(dmis, reg, opt)

#######################################################################
# Define DC Inversion Directives
# ------------------------------
#
# Here we define any directives that are carried out during the inversion. This
# includes the cooling schedule for the trade-off parameter (beta), stopping
# criteria for the inversion and saving inversion results at each iteration.
#

# Apply and update sensitivity weighting as the model updates
update_sensitivity_weighting = directives.UpdateSensitivityWeights()

# Reach target misfit for L2 solution, then use IRLS until model stops changing.
update_IRLS = directives.Update_IRLS(
    max_irls_iterations=20, minGNiter=1, chifact_start=1.
)

# Defining a starting value for the trade-off parameter (beta) between the data
# misfit and the regularization.
starting_beta = directives.BetaEstimate_ByEig(beta0_ratio=2e1)

# Update preconditionner
update_Jacobi = directives.UpdatePreconditioner()

# Options for outputting recovered models and predicted data for each beta.
save_iteration = directives.SaveOutputEveryIteration(save_txt=False)

directives_list = [
    update_sensitivity_weighting,
    update_IRLS,
    starting_beta,
    save_iteration
]

#####################################################################
# Running the DC Inversion
# ------------------------
#
# To define the inversion object, we need to define the inversion problem and
# the set of directives. We can then run the inversion.
#

# Here we combine the inverse problem and the set of directives
dc_inversion = inversion.BaseInversion(inv_prob, directiveList=directives_list)

# Run inversion
recovered_conductivity_model = dc_inversion.run(starting_conductivity_model)

############################################################
# Plotting True and Recovered Conductivity Model
# ----------------------------------------------
#

# Load true conductivity model
true_conductivity_model = np.loadtxt(str(true_conductivity_filename))
true_conductivity_model_log10 = np.log10(true_conductivity_model[ind_active])

# Get L2 and sparse recovered model in base 10
l2_conductivity_model_log10 = np.log10(np.exp(inv_prob.l2model))
recovered_conductivity_model_log10 = np.log10(np.exp(recovered_conductivity_model))

# Plot True Model
fig = plt.figure(figsize=(9, 15))
ax1 = 3 * [None]
ax2 = 3 * [None]
title_str = [
    "True Conductivity Model",
    "Smooth Recovered Model",
    "Sparse Recovered Model",
]

plotting_map = maps.ActiveCells(mesh, ind_active, np.nan)
plotting_model = [
    true_conductivity_model_log10,
    l2_conductivity_model_log10,
    recovered_conductivity_model_log10,
]

for ii in range(0, 3):

    ax1[ii] = fig.add_axes([0.15, 0.73 - 0.3 * ii, 0.66, 0.21])
    mesh.plotImage(
        plotting_map * plotting_model[ii],
        ax=ax1[ii],
        grid=False,
        clim=(
            np.min(true_conductivity_model_log10),
            np.max(true_conductivity_model_log10),
        ),
        pcolor_opts={"cmap": mpl.cm.viridis},
    )
    ax1[ii].set_xlim(-600, 600)
    ax1[ii].set_ylim(-600, 0)
    ax1[ii].set_title(title_str[ii])
    ax1[ii].set_xlabel("x (m)")
    ax1[ii].set_ylabel("z (m)")

    ax2[ii] = fig.add_axes([0.83, 0.73 - 0.3 * ii, 0.02, 0.21])
    norm = mpl.colors.Normalize(
        vmin=np.min(true_conductivity_model_log10),
        vmax=np.max(true_conductivity_model_log10),
    )
    cbar = mpl.colorbar.ColorbarBase(
        ax2[ii],
        norm=norm,
        orientation="vertical",
        cmap=mpl.cm.viridis,
        format="10^%.1f",
    )
    cbar.set_label("$S/m$", rotation=270, labelpad=15, size=12)

plt.show()

###################################################################
# Plotting Predicted DC Data and Misfit
# -------------------------------------
#

# Predicted data from recovered model
dpred = inv_prob.dpred

# Plot
fig = plt.figure(figsize=(9, 13))
data_array = [np.abs(dobs), np.abs(dpred), (dobs - dpred)/std]
plot_title = ["Observed Voltage", "Predicted Voltage", "Normalized Misfit"]
plot_units = ["V/A", "V/A", ""]
scale = ["log", "log", "linear"]

ax1 = 3 * [None]
cax1 = 3 * [None]
cbar = 3 * [None]
cplot = 3 * [None]

for ii in range(0, 3):

    ax1[ii] = fig.add_axes([0.15, 0.72-0.33*ii, 0.65, 0.21])
    cax1[ii] = fig.add_axes([0.81, 0.72-0.33*ii, 0.03, 0.21])
    cplot[ii] = plot_2d_pseudosection(
        survey,
        data_array[ii],
        'tricontourf',
        ax=ax1[ii],
        cax=cax1[ii],
        scale=scale[ii],
        units=plot_units[ii],
        mask_topography=True,
        tricontourf_opts={"levels": 25, "cmap": mpl.cm.viridis},
    )
    ax1[ii].set_title(plot_title[ii])

plt.show()
