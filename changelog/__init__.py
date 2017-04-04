# -*- coding: utf-8 -*-
"""
This is a script to determine which PRs have been merges since the last
release, or between two releases on the same branch.
"""
from __future__ import print_function
import argparse
import re
import os

from collections import namedtuple

import requests

GITHUB_API_URL = 'https://api.github.com'
GITHUB_API_TOKEN = os.environ.get('GITHUB_API_TOKEN', None)
GITHUB_HEADERS = {}
if GITHUB_API_TOKEN is not None:
    GITHUB_HEADERS['Authorization'] = 'token ' + GITHUB_API_TOKEN

Commit = namedtuple('Commit', ['sha', 'message'])
PullRequest = namedtuple('PullRequest', ['number', 'title'])

# Merge commits use a double linebreak between the branch name and the title
MERGE_PR_RE = re.compile(r'^Merge pull request #([0-9]+) from .*\n\n(.*)')
SQUASH_PR_RE = re.compile(r'^(.*) \(#([0-9]+)\)\n\n.*')


class GitHubError(Exception):
    pass


def get_commit_for_tag(owner, repo, tag):
    """ Get the commit sha for a given git tag """
    tag_url = '/'.join([
        GITHUB_API_URL,
        'repos',
        owner, repo,
        'git', 'refs', 'tags', tag
    ])
    tag_json = {}

    while 'object' not in tag_json or tag_json['object']['type'] != 'commit':
        tag_response = requests.get(tag_url, headers=GITHUB_HEADERS)
        tag_json = tag_response.json()

        if tag_response.status_code != 200:
            raise GitHubError("Unable to get tag {}. {}".format(
                tag, tag_json['message']))

        # If we're given a tag object we have to look up the commit
        if tag_json['object']['type'] == 'tag':
            tag_url = tag_json['object']['url']

    return tag_json['object']['sha']


def get_last_commit(owner, repo, branch='master'):
    """ Get the last commit sha for the given repo and branch """
    commits_url = '/'.join([
        GITHUB_API_URL,
        'repos',
        owner, repo,
        'commits'
    ])
    commits_response = requests.get(commits_url, params={'sha': 'master'},
                                    headers=GITHUB_HEADERS)
    commits_json = commits_response.json()
    if commits_response.status_code != 200:
        raise GitHubError("Unable to get commits. {}".format(
            commits_json['message']))

    return commits_json[0]['sha']


def get_last_tag(owner, repo):
    """ Get the last tag for the given repo """
    tags_url = '/'.join([GITHUB_API_URL, 'repos', owner, repo, 'tags'])
    tags_response = requests.get(tags_url, headers=GITHUB_HEADERS)
    tags_response.raise_for_status()
    tags_json = tags_response.json()
    return tags_json[0]['name']


def get_commits_between(owner, repo, first_commit, last_commit):
    """ Get a list of commits between two commits """
    commits_url = '/'.join([
        GITHUB_API_URL,
        'repos',
        owner, repo,
        'compare',
        first_commit + '...' + last_commit
    ])
    commits_response = requests.get(commits_url, params={'sha': 'master'},
                                    headers=GITHUB_HEADERS)
    commits_json = commits_response.json()
    if commits_response.status_code != 200:
        raise GitHubError("Unable to get commits between {} and {}. {}".format(
            first_commit, last_commit, commits_json['message']))

    if 'commits' not in commits_json:
        raise GitHubError("Commits not found between {} and {}.".format(
            first_commit, last_commit))

    commits = [Commit(c['sha'], c['commit']['message'])
               for c in commits_json['commits']]
    return commits


def is_pr(message):
    """ Determine whether or not a commit message is a PR merge """
    return MERGE_PR_RE.search(message) or SQUASH_PR_RE.search(message)


def extract_pr(message):
    """ Given a PR merge commit message, extract the PR number and title """
    merge_match = MERGE_PR_RE.match(message)
    squash_match = SQUASH_PR_RE.match(message)

    if merge_match is not None:
        number, title = merge_match.groups()
        return PullRequest(number=number, title=title)
    elif squash_match is not None:
        title, number = squash_match.groups()
        return PullRequest(number=number, title=title)

    raise Exception("Commit isn't a PR merge, {}".format(message))


def fetch_changes(owner, repo, previous_tag=None, current_tag=None,
                  branch='master'):
    if previous_tag is None:
        previous_tag = get_last_tag(owner, repo)
    previous_commit = get_commit_for_tag(owner, repo, previous_tag)

    current_commit = None
    if current_tag is not None:
        current_commit = get_commit_for_tag(owner, repo, current_tag)
    else:
        current_commit = get_last_commit(owner, repo, branch)

    commits_between = get_commits_between(owner, repo,
                                          previous_commit, current_commit)

    # Process the commit list looking for PR merges
    prs = [extract_pr(c.message) for c in commits_between if is_pr(c.message)]

    if len(prs) == 0 and len(commits_between) > 0:
        raise Exception("Lots of commits and no PRs on branch {}".format(
            branch))

    prs.reverse()
    return prs


def format_changes(owner, repo, prs, markdown=False):
    """ Format the list of prs in either text or markdown """
    lines = []
    for pr in prs:
        number = '#{number}'.format(number=pr.number)
        if markdown:
            link = 'https://github.com/{owner}/{repo}/pull/{number}'.format(
                owner=owner, repo=repo, number=pr.number)
            number = '[{number}]({link})'.format(number=number, link=link)

        lines.append('- {title} {number}'.format(title=pr.title,
                                                 number=number))

    return lines


def generate_changelog(owner, repo, previous_tag=None, current_tag=None,
                       markdown=False, single_line=False):

    prs = fetch_changes(owner, repo, previous_tag, current_tag)
    lines = format_changes(owner, repo, prs, markdown=markdown)

    separator = '\\n' if single_line else '\n'
    return separator.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a CHANGELOG between two git tags based on GitHub"
                    "Pull Request merge commit messages")
    parser.add_argument('owner', metavar='OWNER',
                        help='owner of the repo on GitHub')
    parser.add_argument('repo', metavar='REPO',
                        help='name of the repo on GitHub')
    parser.add_argument('previous_tag', metavar='PREVIOUS', nargs='?',
                        help='previous release tag (defaults to last tag)')
    parser.add_argument('current_tag', metavar='CURRENT', nargs='?',
                        help='current release tag (defaults to HEAD)')
    parser.add_argument('-m', '--markdown', action='store_true',
                        help='output in markdown')
    parser.add_argument('-s', '--single-line', action='store_true',
                        help='output as single line joined by \\n characters')

    args = parser.parse_args()

    changelog = generate_changelog(**vars(args))
    print(changelog)


if __name__ == '__main__':
    main()
