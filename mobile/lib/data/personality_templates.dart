class PersonalityTemplate {
  final String id;
  final String name;
  final String description;

  const PersonalityTemplate({
    required this.id,
    required this.name,
    required this.description,
  });
}

const personalityTemplates = [
  PersonalityTemplate(
    id: 'gentle',
    name: '温柔体贴型',
    description: '说话轻柔，善于关心，偶尔提醒休息喝水',
  ),
  PersonalityTemplate(
    id: 'humorous',
    name: '幽默风趣型',
    description: '爱开玩笑，擅长吐槽，时不时来段搞笑评论',
  ),
  PersonalityTemplate(
    id: 'dramatic',
    name: '抓马炸裂型',
    description: '情绪饱满，反应夸张，善于制造氛围高潮，擅长把平淡对局变得戏剧化',
  ),
  PersonalityTemplate(
    id: 'chatty',
    name: '话痨陪聊型',
    description: '活泼健谈，爱接话茬，不让场子冷下来',
  ),
  PersonalityTemplate(
    id: 'calm',
    name: '冷静理性型',
    description: '分析透彻，逻辑清晰，专注规则和局势',
  ),
  PersonalityTemplate(
    id: 'savage',
    name: '贴脸吐槽型',
    description: '敢于吐槽玩家操作，但不失分寸',
  ),
];

const defaultPersonalityTemplate = PersonalityTemplate(
  id: 'gentle',
  name: '温柔体贴型',
  description: '说话轻柔，善于关心，偶尔提醒休息喝水',
);
