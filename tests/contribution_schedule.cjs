const assert=require('node:assert/strict');
const {project}=require('../mypocket/static/projection.js');
const base={initial:1000,monthly:100,years:3,annualReturn:0,inflation:0};
const close=(a,b)=>assert(Math.abs(a-b)<=Math.max(1,Math.abs(b))*1e-10,`${a} != ${b}`);
let changes=[{startMonth:25,monthly:50},{startMonth:13,monthly:0}];
const before=JSON.stringify(changes);
let r=project({...base,contributionChanges:changes});
close(r.endValue,2800);close(r.contributed,2800);close(r.deposits,1800);close(r.growth,0);
assert.deepEqual(r.points.map(p=>p.contributed),[1000,2200,2200,2800]);
assert.equal(JSON.stringify(changes),before); // Sorting must not mutate caller input.
assert.equal(r.excludedChanges,0);
r=project({...base,years:1,contributionChanges:[{startMonth:1,monthly:200},{startMonth:12,monthly:25}]});
close(r.deposits,2225);close(r.endValue,3225);
r=project({...base,years:2,contributionChanges:[{startMonth:12,monthly:200},{startMonth:13,monthly:300},{startMonth:24,monthly:0}]});
close(r.deposits,4600);close(r.points[1].contributed,2300);close(r.points[2].contributed,5600);
r=project({...base,years:1,contributionChanges:[{startMonth:13,monthly:200}]});
close(r.endValue,2200);assert.equal(r.excludedChanges,1);assert.equal(r.appliedChanges,0);
r=project({...base,years:0,contributionChanges:[{startMonth:1,monthly:200}]});
close(r.endValue,1000);close(r.deposits,0);assert.equal(r.excludedChanges,1);
// Independently sum the terminal value of every month-end deposit.
changes=[{startMonth:1,monthly:30},{startMonth:8,monthly:200},{startMonth:19,monthly:0},{startMonth:27,monthly:50}];
for(const annualReturn of [8,-20,0.000001]){
 r=project({...base,annualReturn,inflation:3,contributionChanges:changes});
 const factor=Math.pow(1+annualReturn/100,1/12);
 let expected=1000*Math.pow(factor,36),deposits=0;
 for(let m=1;m<=36;m++){
  const amount=m<8?30:m<19?200:m<27?0:50;
  expected+=amount*Math.pow(factor,36-m);deposits+=amount;
 }
 close(r.endValue,expected);close(r.contributed,1000+deposits);close(r.growth,expected-1000-deposits);
 close(r.realValue,expected/Math.pow(1.03,3));close(r.points.at(-1).value,expected);
}
r=project({...base,initial:0,monthly:0,years:60,contributionChanges:[{startMonth:720,monthly:1}]});
close(r.endValue,1);close(r.deposits,1);
for(const contributionChanges of [null,{},[{startMonth:0,monthly:1}],[{startMonth:721,monthly:1}],
 [{startMonth:1.5,monthly:1}],[{startMonth:'1',monthly:1}],[{startMonth:1,monthly:-1}],
 [{startMonth:1,monthly:Infinity}],[{startMonth:1,monthly:NaN}],[{startMonth:1,monthly:1e10}],
 [{startMonth:13,monthly:1},{startMonth:13,monthly:2}],[{startMonth:700,monthly:1},{startMonth:700,monthly:1}], [null]]) {
 assert.throws(()=>project({...base,contributionChanges}),RangeError);
}
