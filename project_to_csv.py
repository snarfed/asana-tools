import argparse
import collections
import copy
import csv
import datetime
import dateutil.parser
import json
import os
import re
import sys

import asana

DEBUG = False
DATE_FORMAT = '%Y-%m-%d'
DATETIME_FORMAT = '%Y%m%d_%H%M'

# workspace specific tag IDs
TAGS = {'P0': 29258466050364,
        'P1': 29258466050361,
        'P2': 29258466050359,
       }

parser = argparse.ArgumentParser(description='This script generates a burndown chart from Asana tasks. The asana python module is required.')
parser.add_argument('-i', '--input', help='JSON file representing an Asana project. These can be generated by using the View As JSON feature in Asana.', required=False)
parser.add_argument('-k', '--key', help='Your Asana API key. Attempts to use ASANA_API_KEY environment variable by default.')
parser.add_argument('-p', '--projectid', help='Asana project id to pull tasks from.', required=False)
parser.add_argument('-s', '--start', help='Sprint start date in YYYY-MM-DD.', required=False)
parser.add_argument('-e', '--end', help='Sprint end date in YYYY-MM-DD.', required=False)
parser.add_argument('-b', '--default_estimate', help='Default estimate for tasks without explicit estimates.', required=False, type=float, default=0)
parser.add_argument('-d', '--debug', help='Enable Asana API debugging.', required=False)
args = parser.parse_args()


# time estimatation regex (e.g. [2:1.5], [estimated:actual])
pattern_estimate = '^\s*\[\s*(\?|\d+\.?\d*|\.?\d+)(?:[-:|/\s]+(\d*\.?\d*))?'
# iteration date pattern (e.g. 2014-03-01 - 2014-03-08, start date - end date)
pattern_dates = '\[(20\d{2}-\d{1,2}-\d{1,2})[-:|\s]+(20\d{2}-\d{1,2}-\d{1,2})\]'

# initialize counts
points_estimated, points_actual = 0, 0
estimated_points_completed, actual_points_completed = 0, 0
estimated_by_tag = collections.defaultdict(float)  # these map tag name in TAGS to points
actual_by_tag = collections.defaultdict(float)

# output setup
tasks_list, burndown = [], []
now = datetime.datetime.utcnow()
points_completed_by_date = collections.defaultdict(float)
points_completed_by_date_actual = collections.defaultdict(float)
# this maps date to {tag: points} dict
points_completed_by_date_tag = \
  collections.defaultdict(lambda: collections.defaultdict(float))
tasks_list.append(['assignee', 'task', 'estimated', 'actual', 'created at', 'due on', 'completed at'])

if not args.input and not args.projectid:
    print "An input file or Asana project ID must be specified."
    sys.exit(2)

if args.debug:
    DEBUG = True

if not args.key:
    ASANA_API_KEY = os.environ.get('ASANA_API_KEY')
    if not ASANA_API_KEY:
        print "Please set ASANA_API_KEY in your environment or pass at execution using the -k flag."
        sys.exit(2)
else:
    ASANA_API_KEY = args.key

if args.input:
    f = json.load(open(args.input))
    tasks = f['data']
    if args.start:
        start = args.start
    else:
        start = raw_input("Sprint start date (YYYY-MM-DD): ")
    if args.end:
        end = args.end
    else:
        end = raw_input("Sprint end date (YYYY-MM-DD): ")
elif args.projectid:
    # Asana API reference: https://asana.com/developers/api-reference/users
    client = asana.Client.basic_auth(ASANA_API_KEY)
    asana_project = client.projects.find_by_id(int(args.projectid, 10))
    # parse start and end dates
    match = re.search(pattern_dates, asana_project['name'])
    if match:
        start = match.group(1)
        end = match.group(2)
    else:
        start = args.start or raw_input("Sprint start date (YYYY-MM-DD): ")
        end = args.end or raw_input("Sprint end date (YYYY-MM-DD): ")
    # only a summary of tasks is returned by project query
    # for additional task details, need to query individual tasks
    print "Gathering tasks from '%s'\nhttps://app.asana.com/0/%s" % (asana_project['name'], args.projectid)
    tasks = []
    project_tasks = client.tasks.find_by_project(int(args.projectid, 10))
    for task in project_tasks:
        # can't use print because it adds a space afterward
        sys.stdout.write('.')
        sys.stdout.flush()
        tasks.append(client.tasks.find_by_id(task['id']))
    print

# convert start/end to datetime
start_date = dateutil.parser.parse(start)
end_date = dateutil.parser.parse(end)

# process asana tasks
for task in tasks:
    # task metadata
    name = task['name'].encode('ascii', 'replace')
    completed = task['completed']
    created_at = dateutil.parser.parse(task['created_at']).strftime(DATE_FORMAT)

    try:
        assignee = task['assignee']['name'].encode('ascii', 'replace')
    except TypeError:
        assignee = None
    # dates
    try:
        due_on = dateutil.parser.parse(task['due_on']).strftime(DATE_FORMAT)
    except AttributeError:
        due_on = None
    try:
        completed_at = dateutil.parser.parse(task['completed_at']).strftime(DATE_FORMAT)
    except AttributeError:
        completed_at = None

    # time estimation
    match = re.search(pattern_estimate, name)
    estimated, actual = args.default_estimate, args.default_estimate
    if match:
        if match.group(1) == '?': # unknown
            estimated = 0.0
        else:
            estimated = float(match.group(1))
        if completed:
            actual = float(match.group(2) or estimated)
        else:
            actual = float(match.group(2) or 0.0)
    else:
        for tag in task['tags']:
            if tag['name'].endswith('pts'):
                estimated = actual = float(tag['name'][:-3])

    # tags
    tag = None
    for t in task['tags']:
        if t['name'] in TAGS:
            tag = t['name']
            break
    estimated_by_tag[tag] += estimated
    actual_by_tag[tag] += actual

    if completed:
        estimated_points_completed += estimated
        actual_points_completed += actual
        points_completed_by_date[completed_at] += estimated
        points_completed_by_date_tag[completed_at][tag] += estimated
        points_completed_by_date_actual[completed_at] += actual

    # update totals
    points_estimated += estimated
    points_actual += actual

    tasks_list.append([assignee, name, estimated, actual, created_at, due_on, completed_at])

# dump task list to csv
with open('tasks.csv', 'w') as fp:
    a = csv.writer(fp, delimiter=',')
    a.writerows(tasks_list)

# compute burndown
day_before_start = start_date - datetime.timedelta(days=1)
days = (end_date - start_date).days
points_remaining = points_estimated
points_remaining_actual = points_actual
estimated_by_tag_remaining = copy.deepcopy(estimated_by_tag)
current_date = start_date
days_remaining = days
avg_points_per_day = points_estimated / days

tags = sorted(estimated_by_tag.keys(), key=lambda t: t or 'ZZZ')
burndown.append(['date', 'estimated', 'actual', 'ideal'] + tags)
while current_date <= end_date:
    day = current_date.strftime(DATE_FORMAT)
    points_remaining -= points_completed_by_date[day]
    points_remaining_actual -= points_completed_by_date_actual[day]
    for tag in estimated_by_tag_remaining:
        estimated_by_tag_remaining[tag] -= points_completed_by_date_tag[day][tag]

    # linear
    if days_remaining == days:
        linear_burn = points_estimated
    else:
        linear_burn = days_remaining * avg_points_per_day

    if current_date <= datetime.datetime.today():
        values = ([day, points_remaining, points_remaining_actual, linear_burn] +
                  [estimated_by_tag_remaining[tag] for tag in tags])
    else:
        values = [day, None, None, linear_burn]

    burndown.append(values)
    days_remaining -= 1
    current_date += datetime.timedelta(days=1)

with open('burndown.csv', 'w') as fp:
    a = csv.writer(fp, delimiter=',')
    a.writerows(burndown)

# stats
completed_percentage = round((float(estimated_points_completed) / points_estimated) * 100.0, 2)

print "Sprint from %s to %s (%s days)" % (start, end, days)
print "Estimated: %s" % points_estimated
print "Actual: %s" % points_actual
print "Completed [Estimated]: %s (%s%%)" % (estimated_points_completed, completed_percentage)
print "Completed [Actual]: %s" % actual_points_completed
print "Tags:"
for tag in sorted(set(estimated_by_tag.keys() + actual_by_tag.keys())):
    estimated = estimated_by_tag.get(tag)
    actual = actual_by_tag.get(tag)
    print '  %s: estimated %s (%s%%), actual %s (%s%%)' % (
        tag, estimated, round((float(estimated) / points_estimated) * 100.0, 2),
        actual, round((float(actual) / points_actual) * 100.0, 2))

# Generate chart URL using Google Image Charts API
# https://google-developers.appspot.com/chart/image/docs/making_charts
start_date.strftime('%m-%d')
now_str = datetime.datetime.now().strftime(DATE_FORMAT)
print ('Burndown chart: https://chart.googleapis.com/chart?'
       'cht=lc&chds=a&chs=600x400&chxt=x,y&chxs=0|1&chxr=&'
       'chxl=0:|%s|%s&chd=t:%s' %
       (start_date.strftime('%b%%20%d'), end_date.strftime('%b%%20%d'),
        ','.join(str(day[1]) if day[0] <= now_str else '_' for day in burndown)))
