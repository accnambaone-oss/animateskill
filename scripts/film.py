"""Local 24 fps shot renderer, assembler and review-aware exporter."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from PIL import Image
from sound import mix


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def digest(path):
    with path.open('rb') as source:
        h = hashlib.sha256()
        for chunk in iter(lambda: source.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def valid(path, size):
    try:
        with Image.open(path) as img:
            if img.size != size or img.format != 'PNG':
                return False
            img.verify()
        return True
    except (OSError, ValueError, SyntaxError):
        return False


def render(shot, source, cache, options, size):
    count = shot['frames']
    if type(count) is not int or count < 1:
        raise ValueError('Shot frame count must be a positive integer')
    if not (source/'index.html').is_file():
        raise FileNotFoundError(source/'index.html')
    files = sorted(p for p in source.rglob('*') if p.is_file())
    fingerprint = [(p.relative_to(source).as_posix(), digest(p)) for p in files]
    key = hashlib.sha256(json.dumps([fingerprint, size, count, options.engine,
        options.engine_version, digest(Path(__file__))]).encode()).hexdigest()
    dest = cache/key
    dest.mkdir(parents=True, exist_ok=True)
    paths = [dest/f'{i:08}.png' for i in range(count)]
    missing = [i for i,p in enumerate(paths) if not valid(p,size)]
    if not missing:
        return paths
    if options.engine == 'svg':
        run('node', Path(__file__).with_name('svg_frames.cjs'), source/'index.html',
            dest, *size, input=json.dumps(missing), text=True)
    elif options.engine == 'browser':
        from playwright.sync_api import sync_playwright
        with sync_playwright() as runner:
            browser = runner.chromium.launch(executable_path=options.chromium)
            page = browser.new_page(viewport=dict(width=size[0],height=size[1]))
            page.goto((source/'index.html').as_uri())
            page.evaluate('document.fonts.ready')
            for i in missing:
                page.evaluate('i => window.drawFrame(i)',i)
                tmp = dest/'pending.png'
                page.screenshot(path=str(tmp), animations='allow')
                tmp.replace(paths[i])
            browser.close()
    else:
        with tempfile.TemporaryDirectory(dir=cache) as work:
            out = Path(work)/'sequence'
            run(options.hyperframes, 'render', source, '--format','png-sequence',
                '--fps','24','--workers','1','--output',out)
            images = sorted(out.rglob('*.png'),key=lambda p: [
                int(x) if x.isdigit() else x for x in re.split(r'(\d+)',p.as_posix())])
            if len(images)!=count or any(not valid(p,size) for p in images):
                raise ValueError(f'HyperFrames produced an unexpected sequence in {out}')
            for i in missing:
                os.replace(images[i],paths[i])
    return paths


def check_video(path, count, size):
    result = run('ffprobe','-v','error','-count_frames','-show_streams','-of','json',
                 path, capture_output=True)
    streams = json.loads(result.stdout)['streams']
    video = next(s for s in streams if s['codec_type']=='video')
    audio = next(s for s in streams if s['codec_type']=='audio')
    if (int(video['nb_read_frames'])!=count or video['avg_frame_rate']!='24/1'
            or [video['width'],video['height']]!=list(size)
            or abs(float(audio['duration'])-count/24)>.05):
        raise ValueError('Export stream verification failed')
    return dict(frames=count, fps=24, width=size[0], height=size[1], seconds=count/24,
                sha256=digest(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path)
    parser.add_argument('--engine',choices=['hyperframes','browser','svg'],default='hyperframes')
    parser.add_argument('--hyperframes',default='hyperframes')
    parser.add_argument('--chromium')
    parser.add_argument('--approve-reviewed',action='store_true')
    opt = parser.parse_args()
    root = opt.project.resolve().parent
    project = json.loads(opt.project.read_text(encoding='utf8'))
    size = (project['width'],project['height'])
    if any(type(v) is not int or v<2 or v%2 for v in size):
        raise ValueError('H.264 requires positive even dimensions')
    if opt.engine=='hyperframes':
        opt.engine_version=run(opt.hyperframes,'--version',capture_output=True,text=True).stdout.strip()
    elif opt.engine=='svg':
        opt.engine_version=run('node','-p',"require('sharp').versions.vips",capture_output=True,text=True).stdout.strip()+digest(Path(__file__).with_name('svg_frames.cjs'))
    elif opt.chromium:
        opt.engine_version=run(opt.chromium,'--version',capture_output=True,text=True).stdout.strip()
    else:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            opt.engine_version=p.chromium.executable_path
    build = root/'build'
    cache = build/'frames'
    cache.mkdir(parents=True,exist_ok=True)
    frames = []
    for shot in project['shots']:
        folder=(root/shot['dir']).resolve()
        if build==folder or build.is_relative_to(folder):
            raise ValueError('Each shot must be in its own source directory outside build')
        frames.extend(render(shot,folder,cache,opt,size))
    if not frames:
        raise ValueError('Empty timeline')
    mix(project,root,build,len(frames))
    state = [str(p.relative_to(cache)) for p in frames]
    key = hashlib.sha256(json.dumps([state,digest(build/'mix.wav'),
        digest(build/'subtitles.srt')]).encode()).hexdigest()
    record = build/'verification.json'
    draft = build/'draft.mp4'
    report = json.loads(record.read_text()) if record.exists() else {}
    if (report.get('key')!=key or not draft.exists() or digest(draft)!=report.get('sha256')
        or not (build/'motion.mp4').exists()):
        with tempfile.TemporaryDirectory(dir=build) as temp:
            stage=Path(temp)
            for i,p in enumerate(frames):
                try:
                    os.link(p,stage/f'{i:08}.png')
                except OSError:
                    shutil.copyfile(p,stage/f'{i:08}.png')
            base=['ffmpeg','-hide_banner','-loglevel','error','-y']
            run(*base,'-framerate','24','-i',stage/'%08d.png','-c:v','libx264',
                '-pix_fmt','yuv420p','-preset','veryfast','-crf','16',stage/'motion.mp4')
            args=['-c:v','copy']
            if project.get('speech'):
                args=['-vf','subtitles=subtitles.srt','-c:v','libx264','-crf','18']
            run(*base,'-i',stage/'motion.mp4','-i',build/'mix.wav',*args,
                '-c:a','aac','-b:a','144k','-movflags','+faststart',stage/'draft.mp4',cwd=build)
            report=check_video(stage/'draft.mp4',len(frames),size)
            report.update(key=key,engine=opt.engine,status='draft')
            for name in ['motion.mp4','draft.mp4']:
                (stage/name).replace(build/name)
            record.write_text(json.dumps(report,indent=2))
    (build/'manifest.json').write_text(json.dumps(dict(frames=state, shots=project['shots'])))
    if opt.approve_reviewed:
        review=root/'review.md'
        if (not review.exists() or report['sha256'] not in review.read_text(encoding='utf8')
                or not review.read_text(encoding='utf8').strip()):
            raise ValueError('Record playback observations and current draft SHA-256 in review.md')
        shutil.copyfile(draft,build/'final.pending.mp4')
        (build/'final.pending.mp4').replace(build/'final.mp4')
        report.update(status='reviewed',review_sha256=digest(review))
        record.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
