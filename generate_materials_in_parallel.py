# %%
import argparse
import concurrent.futures
import os
import datetime

from data_myMeshes import generate_material_geometry
from helper_funcs import wallpaper_groups
from helper_funcs import new_path

# directory to save the generated materials
# if it does not exist, it will be created
data_dir = os.path.join('data', 'dataset1', 'generated_geometries')

figures = 1
verbose = False

# Define a function to generate one material geometry
# This function will be called multiple times in parallel
# It will create a new folder for each geometry and save the generated material there
def _generate_material_geometry(group, shape):

    # keep trying until succesful
    for i in range(100):
        # Create new folder for figures
        date_time_string = str(datetime.datetime.now()).replace(' ', '_').replace(':', '-')
        name = f'{group}_{shape}_{date_time_string}'

        save_dir = new_path(os.path.join(data_dir, name), always_number=False)
        # safely create new folder
        while(True):
            if not os.path.exists(save_dir):
                os.mkdir(save_dir)
                break
            else:
                save_dir = new_path(os.path.join(data_dir, name), always_number=False)

        try:
            # generate new material
            generate_material_geometry(group, shape, verbose=verbose, figures=figures, save_dir=save_dir)
        except Exception as e:
            with(open(new_path(os.path.join(save_dir, 'error.txt')), 'w')) as f:
                f.write(repr(e))
            print(repr(e))
        else:   # if no exception -> successful! -> break loop
            break
    else:  # no break -> failed 100 times
        print(f'Failed to generate {group} {shape} 100 times!')

# %%
def print_options():
    for group in wallpaper_groups:
        print(group)
        for shape in wallpaper_groups[group]['fundamental domain parameters']:
            print('  -', shape)


def build_args(n=60):
    args1 = []
    args2 = []
    for group in wallpaper_groups:
        print(group)
        args1.extend([group]*n)
        shapes = wallpaper_groups[group]['fundamental domain parameters'].keys()
        shapes = list(shapes)
        for i in range(n):
            args2.append(shapes[i % len(shapes)])  # cycle through shapes

    assert len(args1) == len(args2), 'Length of args1 and args2 should be the same'
    return args1, args2

# %%
def parse_args():
    parser = argparse.ArgumentParser(description='Generate material geometries in parallel.')
    parser.add_argument(
        '--data-dir',
        default=data_dir,
        help='Directory where generated geometry folders will be saved.',
    )
    return parser.parse_args()


def main():
    global data_dir

    args = parse_args()
    data_dir = args.data_dir
    os.makedirs(data_dir, exist_ok=True)

    print_options()
    args1, args2 = build_args()

    with concurrent.futures.ProcessPoolExecutor(max_workers=6) as executor:
        for results in executor.map(_generate_material_geometry, args1, args2):
            pass

    error_dir = os.path.join(data_dir, 'error_geometries')
    os.makedirs(error_dir, exist_ok=True)

    # move all folders with an error_00.txt file to error_geometries folder
    print('Failed:')
    for folder in os.listdir(data_dir):
        if folder == 'error_geometries':
            continue
        if not os.path.isdir(os.path.join(data_dir, folder)):
            continue
        if not os.path.exists(os.path.join(data_dir, folder, 'error_00.txt')):
            continue

        print(folder)

        # move folder to error_geometries folder
        os.rename(os.path.join(data_dir, folder), os.path.join(data_dir, 'error_geometries', folder))

if __name__ == '__main__':
    main()
