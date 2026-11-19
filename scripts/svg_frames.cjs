// Capture self-contained, trusted SVG scenes whose drawFrame sets element attributes.
const fs = require('node:fs');
const vm = require('node:vm');
const sharp = require('sharp');
const [source, destination, width, height] = process.argv.slice(2);
const page = fs.readFileSync(source, 'utf8');
const original = page.match(/<svg\b[^]*?<\/svg>/i)?.[0];
if (!original) throw Error('Expected an inline SVG scene');
const escape = value => String(value).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;');
const changes = new Map();
const document = {getElementById(id) {
  return {setAttribute(key,value) {
    if (!/^[\w:-]+$/.test(key)) throw Error('Invalid SVG attribute');
    if (!changes.has(id)) changes.set(id, new Map());
    changes.get(id).set(key,escape(value));
  }};
}};
const context = vm.createContext({document,window:{}});
for (const script of page.matchAll(/<script\b[^>]*>([^]*?)<\/script>/gi)) {
  vm.runInContext(script[1],context);
}
async function render(){
  for(const i of JSON.parse(fs.readFileSync(0,'utf8'))){
    changes.clear();
    context.window.drawFrame(i);
    const svg=original.replace(/<[\w:-]+\b[^>]*>/g, tag=>{
      const id=tag.match(/\bid="([^"]+)"/)?.[1];
      for(const [key,value] of changes.get(id) || []){
        const attr=new RegExp('\\s'+key+'="[^"]*"');
        if(attr.test(tag)) tag=tag.replace(attr,()=>` ${key}="${value}"`);
        else tag=tag.replace(/(\/?>)$/,(_,end)=>` ${key}="${value}"${end}`);
      }
      return tag;
    });
    const file=destination+'/'+String(i).padStart(8,'0')+'.png';
    await sharp(Buffer.from(svg)).resize(Number(width),Number(height)).png().toFile(file+'.part');
    fs.renameSync(file+'.part',file);
  }
}
render().catch(e=>{console.error(e);process.exitCode=1});
