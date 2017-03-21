# [test.superdesk.org](https://test.superdesk.org)

**Authorization via Github, also people must be in [Superdesk Organisation][sd-people] to get access here.**

[sd-people]: https://github.com/orgs/superdesk/people

The main page contains the list of enabled repositories.

A repository page contains a list of **Pull Requests** and **Branches** with related links:
- `[instance]` link to the test instance
- `[deploy]` runs only deployment step
- `[restart]` runs failed/waiting steps if they are exist or runs all steps
- `[restart all]` runs all steps (including `build` step)
- `[reset db]` resets databases for the test instance

![A repository page](images/ci-repo-page.png)

## Test instance

**There are no real emails (by default),** all emails are stored in log files and can be found by url: `<domain>/mail`.

**Server logs** for particular instance can be found by url `<domain>/logs`.

For example for `sd-master`:
- https://sd-master.test.superdesk.org/mail/ emails
- https://sd-master.test.superdesk.org/logs/ logs

# Github integration

After webhook is invoked by Github, `fireq` uses [Github API][gh-statuses] to post statuses.

[gh-statuses]: https://developer.github.com/v3/repos/statuses/

![Show all checks](images/gh-show-all-checks.png)
![Statuses](images/gh-checks.png)

## Minimal set of statuses
```
├─ fire:build       # build code for the proper git commit
├─ fire:www         # deploy the test instance, contains the link if successful
├─ fire:restart     # the way to restart failed (or all) steps from Github interface
```

# Admin area
You need `SSH` access to `host7.sourcefabric.org`.

All steps can be run from shell, web server just calls this scripts in background.
```sh
cd /opt/fireq       # code
./fire -h           # help messages are pretty detailed

vim config.json     # config
./fire config       # show all config values (with defaults ones)

# run ci for superdesk/superdesk master branch
./fire ci sd master
./fire ci sd master -t build -t www
./fire ci sd master --all

# run ci for superdesk/superdesk-ntb master branch
./fire ci ntb master

# shortcut for ssh-ing to container with no interruption and fully-worked shell
./fire lxc-ssh sd-master

# next two command are running by cron /etc/cron.d/fireq
./fire gh-clean # clean containers by checking Github for alive PRs and branches
./fire gh-pull  # checks if ci have been runnnig for all PRs and branches

# lxc containers uses zfs pools
zfs list
ll /var/tmp/zpool.*
```

`fireq.cli` uses [mustache][mustache] templates in `tpl` directory to generate straightforward bash scripts.

[mustache]: https://mustache.github.io/mustache.5.html

## Github statuses
`./fire ci` posts proper statuses to Github if config values are filled:
```json
"no_statuses": false
"github_auth": "<username>:<token from https://github.com/settings/tokens>"
```

## Env variables
There are init files in [tpl/init][init], which invoked after build step
```sh
name=sd-newthing
cat <<EOF2 > tpl/init/$name.sh
{{>init/sd.sh}}

cat <<"EOF" >> {{config}}
# there are variables
WEBHOOK_PERSONALIA_AUTH=1234
EOF
EOF2

# deploy with new init
./fire ci sd new-thing -t www

# see example
cat tpl/init/sd-naspeh.sh
```

[init]: https://github.com/superdesk/fireq/tree/master/tpl/init

## fireq.web - webhook and dashboard
```sh
systemd restart fireq
cat /etc/systemd/system/fireq.service

# systemd service runs gunicorn like this
gunicorn fireq.web:app --bind localhost:8080 --worker-class aiohttp.worker.GunicornWebWorker
```

### Webhook
```
Payload URL: https://test.superdesk.org/hook
Secret: <"secret" from config.json>
```

### Login via Github
Fill `github_id` and `github_secret` config values from [one of applications.][gh-apps]
[gh-apps]: https://github.com/organizations/superdesk/settings/applications

## Upgrade
```sh
cd /opt/fireq
git pull

# mostly needed if fireq/web.py is changed
systemd restart fireq
```

## Troubleshooting
If `[restart]` and `[restart-all]` is not working from Dashboard, then look at proper logs to find an issue.

```sh
# Sometimes "lxc-destroy" is not working properly, because underlying ZFS
zfs destroy lxc/sd-something
lxc-destroy -fn sd-something

# Sometimes it needs to run "lxc-destroy" manually for some reason
lxc-destroy -fn sd-something
```