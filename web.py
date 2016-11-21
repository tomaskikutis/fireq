#!/usr/bin/env python3
import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import math
import os
import re
import warnings
from asyncio.subprocess import PIPE
from pathlib import Path

# from aiofiles import open as async_open
from aiohttp import web, ClientSession

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    datefmt='%H:%M:%S',
    format='%(asctime)s %(message)s'
)
root = Path(__file__).resolve().parent
conf = None


def init_conf():
    global conf
    with open('config.json', 'r') as f:
        conf = json.loads(f.read())

    defaults = [
        ('debug', False),
        ('debug_aio', False),
        ('sdbase', 'sdbase'),
        ('domain', 'localhost'),
        ('logurl', lambda c: 'http://%s/' % c['domain']),
        ('e2e_count', 4),
    ]
    for key, value in defaults:
        if callable(value):
            value = value(conf)
        conf.setdefault(key, value)


def init_loop(loop=None):
    init_conf()
    if not loop:
        loop = asyncio.get_event_loop()

    if conf['debug_aio']:
        # Enable debugging
        loop.set_debug(True)

        # Make the threshold for "slow" tasks very very small for
        # illustration. The default is 0.1, or 100 milliseconds.
        loop.slow_callback_duration = 0.001

        # Report all mistakes managing asynchronous resources.
        warnings.simplefilter('always', ResourceWarning)
    return loop


def get_app():
    app = web.Application()
    init_loop(app.loop)
    app.router.add_post('/', hook)
    app.router.add_static('/push', root / 'push', show_index=True)
    return app


def pretty_json(obj):
    return json.dumps(obj, indent=2, sort_keys=True)


async def sh(cmd, ctx, *, logfile=None):
    cmd = 'set -ex; cd %s; %s' % (root, cmd.format(**ctx))
    logfile = logfile or ctx['logfile']
    if logfile:
        cmd = (
            '({cmd}) >> {path} 2>&1;'
            'code=$?;'
            'cat {path} | aha -w --black > {path}.htm;'
            'exit $code'
            .format(cmd=cmd, path=ctx['logpath'] + logfile)
        )
    log.info(cmd)
    proc = await asyncio.create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    out, err = await proc.communicate()
    code = proc.returncode
    log.info('code=%s\n%s\nout=%r\nerr=%r', code, cmd, out, err)
    return code


def get_ctx(headers, body, **extend):
    event = headers.get('X-Github-Event')
    if event == 'pull_request':
        if body['action'] not in ('opened', 'reopened', 'synchronize'):
            return {}
        sha = body['pull_request']['head']['sha']
        name = body['number']
        prefix = 'pr'
        env = 'repo_pr=%s repo_sha=%s' % (body['number'], sha)
    elif event == 'push':
        sha = body['after']
        branch = body['ref'].replace('refs/heads/', '')
        name = re.sub('[^a-z0-9]', '', branch)
        prefix = ''
        env = 'repo_sha=%s repo_branch=%s' % (sha, branch)
    else:
        return {}

    # mean it has been delated
    if sha == '0000000000000000000000000000000000000000':
        return {}

    repo = body['repository']['full_name']
    if repo.startswith('naspeh-sf'):
        # For testing purpose
        repo = repo.replace('naspeh-sf', 'superdesk')

    if repo == 'superdesk/superdesk':
        endpoint = 'superdesk-dev/master'
        prefix = 'sd' + prefix
        checks = {'targets': ['flake8', 'npmtest']}
    elif repo == 'superdesk/superdesk-core':
        endpoint = 'superdesk-dev/core'
        prefix = 'sds' + prefix
        checks = {
            'targets': ['docs', 'flake8', 'nose', 'behave'],
            'env': 'frontend='
        }
    elif repo == 'superdesk/superdesk-client-core':
        endpoint = 'superdesk-dev/client-core'
        prefix = 'sdc' + prefix
        checks = {'targets': ['e2e', 'npmtest', 'docs']}
    else:
        log.warn('Repository %r is not supported', repo)
        return {}

    name = '%s-%s' % (prefix, name)
    uniq = (name, sha[:10])
    name_uniq = '%s-%s' % uniq
    host = '%s.%s' % (name, conf['domain'])
    path = 'push/%s/%s' % uniq
    env += ' repo_remote=%s host=%s' % (body['repository']['clone_url'], host)
    ctx = {
        'sha': sha,
        'name': name,
        'name_uniq': name_uniq,
        'host': '%s.%s' % (name, conf['domain']),
        'path': path,
        'sdbase': conf['sdbase'],
        'endpoint': endpoint,
        'checks': checks,
        'logpath': (
            '{path}/{time:%Y%m%d-%H%M%S}/'
            .format(path=path, time=dt.datetime.now())
        ),
        'logfile': 'build.log',
        'env': env,
        'statuses_url': body['repository']['statuses_url'].format(sha=sha),
        'install': True
    }
    ctx.update(**extend)
    ctx.update(
        clean=ctx.get('clean') and '--clean' or '',
        clean_web=ctx.get('clean_web') and '--clean-web' or '',
        logurl=conf['logurl'] + ctx['logpath']
    )
    os.makedirs(ctx['logpath'], exist_ok=True)
    log.info(pretty_json(ctx))
    return ctx


async def post_status(ctx, state=None, extend=None, code=None):
    assert state is not None or code is not None
    if code is not None:
        state = 'success' if code == 0 else 'failure'

    logfile = ctx['logfile']
    if state != 'pending':
        logfile += '.htm'

    data = {
        'state': state,
        'target_url': ctx['logurl'] + logfile,
        'description': 'Superdesk Deploy',
        'context': 'naspeh-sf/deploy/build'
    }
    if extend:
        data.update(extend)
    b64auth = base64.b64encode(conf['github_auth'].encode()).decode()
    headers = {'Authorization': 'Basic %s' % b64auth}
    async with ClientSession(headers=headers) as s:
        async with s.post(ctx['statuses_url'], data=json.dumps(data)) as resp:
            body = pretty_json(await resp.json())
            log.info('Posted status: %s\n%s', resp.status, body)
            if resp.status != 201:
                log.warn(pretty_json(data))
            path = '{path}{target}-{state}.json'.format(
                path=ctx['logpath'],
                target=data['context'].rsplit('/', 1)[1],
                state=state
            )
            with open(path, 'w') as f:
                f.write(body)
            # async with async_open(path, 'w') as f:
            #     await f.write(body)
            return resp


def chunked(l, n):
    # inspiration: http://stackoverflow.com/a/24484181
    chunksize = int(math.ceil(len(l) / n))
    return (l[i * chunksize:i * chunksize + chunksize] for i in range(n))


async def check_e2e(ctx):
    ctx.update(name_e2e='%s-e2e' % ctx['name_uniq'])
    code = await sh('./fire lxc-copy -sb {name_uniq} {name_e2e}', ctx)
    if code != 0:
        return code

    pattern = '*' * 30
    cmd = '''
    cd {r} &&
    ./fire r -e {endpoint} --lxc-name={name_e2e} -a 'pattern="{p}" do_specs'
    '''.format(r=root, p=pattern, **ctx)
    proc = await asyncio.create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        log.error('ERROR: %s', err)
        return 1
    specs = out.decode().rsplit(pattern, 1)[-1].split()

    targets = chunked(specs, conf['e2e_count'])
    targets = [
        {
            'target': 'e2e--part%s' % num,
            'parent': 'e2e',
            'env': 'specs=%s' % ','.join(t)
        } for num, t in enumerate(targets, 1)
    ]
    code = await run_targets(ctx, targets)
    return code


async def run_target(ctx, target):
    cmd = '''
    lxc={name_uniq}-{t};
    ./fire lxc-copy {clean} -s -b {name_uniq} $lxc
    ./fire r --lxc-name=$lxc --env="{env}" -e {endpoint} -a "{p}=1 do_checks"
    '''

    if isinstance(target, dict):
        parent = target['parent']
        env = target['env']
        target = target['target']
    else:
        parent, env = target, ''
    env = ' '.join(i for i in (env, ctx['env']) if i)

    logfile = 'check-%s.log' % target
    status = {
        'target_url': ctx['logurl'] + logfile,
        'context': 'naspeh-sf/deploy/check-%s' % target
    }
    await post_status(ctx, 'pending', extend=status)
    if target == 'e2e':
        code = await check_e2e(dict(ctx, logfile=logfile))
    else:
        c = dict(ctx, p=parent, t=target, env=env)
        code = await sh(cmd, c, logfile=logfile)
    await post_status(ctx, code=code, extend=status)
    log.info('Finished %r with %s', target, code)
    return code


async def wait_for(proces):
    failed = 0
    for f in asyncio.as_completed(proces):
        failed = await f or failed
    return failed


async def run_targets(ctx, targets):
    proces = [run_target(dict(ctx), t) for t in targets]
    return await wait_for(proces)


async def checks(ctx):
    async def clean(code):
        await post_status(ctx, code=code)
        await sh(
            './fire lxc-clean "^{name_uniq}-";'
            '[ -z "{clean}" ] || ./fire lxc-rm {name_uniq}',
            ctx
        )

    targets = ctx['checks']['targets']
    env = ctx['checks'].get('env', '')
    env = ' '.join(i for i in (env, ctx['env']) if i)

    if ctx['install']:
        code = await sh('''
        ./fire lxc-copy -s -b {sdbase} {clean} {name_uniq}
        ./fire i --lxc-name={name_uniq} --env="{env}" -e {endpoint};
        lxc-stop -n {name_uniq};
        ''', dict(ctx, env=env))

        if code:
            await clean(code)
            return code

    code = await run_targets(ctx, targets)
    await clean(code)
    return code


async def pubweb(ctx):
    logfile = 'web.log'
    status = {
        'target_url': ctx['logurl'] + logfile,
        'context': 'naspeh-sf/deploy/web'
    }
    await post_status(ctx, 'pending', extend=status)
    code = await sh('''
    ./fire lxc-copy -s -b {sdbase} {clean_web} {name}
    ./fire i --lxc-name={name} --env="{env}" -e {endpoint} --prepopulate;
    name={name} host={host} \
        . superdesk-dev/nginx.tpl > /etc/nginx/instances/{name};
    nginx -s reload || true
    ''', ctx, logfile=logfile)

    if code == 0:
        status['target_url'] = 'http://%s.%s' % (ctx['name'], conf['domain'])
    await post_status(ctx, code=code, extend=status)
    return code


async def build(ctx):
    await post_status(ctx, 'pending')
    proces = [t(ctx) for t in (pubweb, checks)]
    return await wait_for(proces)


def get_signature(body):
    sha1 = hmac.new(conf['secret'].encode(), body, hashlib.sha1).hexdigest()
    return 'sha1=' + sha1


async def hook(request):
    body = await request.read()
    check_signature = hmac.compare_digest(
        get_signature(body),
        request.headers.get('X-Hub-Signature', '')
    )
    if not check_signature:
        return web.Response(status=400)

    body = await request.json()
    headers = dict(request.headers.items())
    del headers['X-Hub-Signature']
    log.info('%s\n\n%s', pretty_json(headers), pretty_json(body))
    ctx = get_ctx(headers, body, clean=True)
    if ctx:
        os.makedirs(ctx['logpath'], exist_ok=True)
        with open(ctx['path'] + '/request.json', 'w') as f:
            f.write(pretty_json([headers, body]))
        # async with async_open(ctx['path'] + '/request.json', 'w') as f:
        #     await f.write(pretty_json([headers, body]))

        request.app.loop.create_task(build(ctx))
    return web.json_response('OK')


app = get_app()
if __name__ == '__main__':
    web.run_app(app, port=os.environ.get('PORT', 8080))