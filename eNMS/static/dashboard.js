/*
global
call: false
*/

const defaultProperties = {
  device: "model",
  link: "model",
  user: "name",
  service: "vendor",
  workflow: "vendor",
  task: "status",
};

function parseData(data) {
  let result = [];
  for (const [key, value] of Object.entries(data)) {
    result.push({
      value: value,
      name: key,
    });
  }
  return result;
}

$(function() {
  const diagrams = {};

  call("/count_models", function(result) {
    for (const type of Object.keys(defaultProperties)) {
      $(`#count-${type}`).text(result.counters[type]);
    }
    for (const [type, objects] of Object.entries(result.properties)) {
      const diagram = echarts.init(document.getElementById(type));
      drawDiagrams(diagram, parseData(objects));
      diagrams[type] = diagram;
    }
  });

  $.each(defaultProperties, function(type, property) {
    $(`#${type}-properties`).on("change", function() {
      call(`/counters/${this.value}/${type}`, function(objects) {
        drawDiagrams(diagrams[type], parseData(objects));
      });
    });
  });
});
