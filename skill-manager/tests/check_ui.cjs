// 只做内联脚本语法与 Vue 模板编译检查，不启动浏览器或服务。
process.on('uncaughtException', e => { console.error(e.message); process.exit(1); });
const fs = require('fs');
const vm = require('vm');
const base = require('path').join(__dirname, '../assets/');
const html = fs.readFileSync(base + 'index.html', 'utf8');
let count = 0;
for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
  if (match[1].trim()) { new vm.Script(match[1].replace('__SKILL_SESSION__', '{"token":"fixture","write":false}')); count++; }
}
const context = { console, document: { createElement: () => ({ set innerHTML(value) { this.textContent = value.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'"); } }) } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(base + 'vendor/vue-3.4.21.global.prod.js', 'utf8'), context);
const start = html.indexOf('<div id="app"');
const end = html.lastIndexOf('<script>');
const template = html.slice(start, end);
const errors = [];
context.Vue.compile(template, { onError: e => errors.push(e.message), decodeEntities: value => value.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/&quot;/g, String.fromCharCode(34)) });
if (errors.length) throw new Error(errors.join('\n'));
console.log(`${count} inline scripts parse; Vue template compiles`);
