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


def build_args(n=60, groups=None, shape=None):
    args1 = []
    args2 = []
    groups = groups or wallpaper_groups.keys()
    for group in groups:
        print(group)
        args1.extend([group]*n)
        shapes = wallpaper_groups[group]['fundamental domain parameters'].keys()
        shapes = list(shapes)
        if shape is not None:
            shapes = [shape]
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
    parser.add_argument(
        '-g',
        '--group',
        choices=sorted(wallpaper_groups),
        help='Generate only this wallpaper group. By default all groups are generated.',
    )
    parser.add_argument(
        '-s',
        '--shape',
        help='Generate only this shape. Requires --group because valid shapes depend on the group.',
    )
    parser.add_argument(
        '-n',
        '--num-per-group',
        type=int,
        default=60,
        help='Number of geometries to generate per selected group.',
    )
    parser.add_argument(
        '-w',
        '--max-workers',
        type=int,
        default=6,
        help='Maximum number of parallel worker processes.',
    )
    parser.add_argument(
        '-f',
        '--figures',
        type=int,
        choices=[0, 1, 2],
        default=figures,
        help='How many figures to save: 0 none, 1 important figures, 2 all debug figures.',
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        help='Print verbose output while generating geometries.',
    )
    return parser.parse_args()


def validate_args(args):
    if args.num_per_group < 1:
        raise ValueError('--num-per-group must be at least 1')
    if args.max_workers < 1:
        raise ValueError('--max-workers must be at least 1')
    if args.shape is not None and args.group is None:
        raise ValueError('--shape requires --group because valid shapes depend on the group')
    if args.shape is not None:
        shapes = wallpaper_groups[args.group]['fundamental domain parameters']
        if args.shape not in shapes:
            valid_shapes = ', '.join(shapes)
            raise ValueError(f'{args.shape!r} is not valid for group {args.group!r}. Valid shapes: {valid_shapes}')


def main():
    global data_dir, figures, verbose

    args = parse_args()
    validate_args(args)

    data_dir = args.data_dir
    figures = args.figures
    verbose = args.verbose
    os.makedirs(data_dir, exist_ok=True)

    groups = [args.group] if args.group is not None else None
    if groups is None:
        print_options()
    args1, args2 = build_args(n=args.num_per_group, groups=groups, shape=args.shape)

    with concurrent.futures.ProcessPoolExecutor(max_workers=args.max_workers) as executor:
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
