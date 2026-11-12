import { useState } from 'react';
import { Button, Card, Form, Input } from 'antd';

const storageKey = 'rtl.review.preference';

function loadPreference() {
  try {
    return window.localStorage.getItem(storageKey) ?? '';
  } catch {
    return '';
  }
}

export default function Review() {
  const [saved, setSaved] = useState(loadPreference);
  const [error, setError] = useState('');

  function save({ preference }) {
    try {
      window.localStorage.setItem(storageKey, preference);
      setSaved(preference);
      setError('');
    } catch {
      setError('当前浏览器无法保存本地偏好。');
    }
  }

  return (
    <>
      <h1>工作台</h1>
      <Card className="prototype-card" title="本地偏好">
        <Form
          layout="vertical"
          initialValues={{ preference: saved }}
          onFinish={save}
        >
          <Form.Item
            name="preference"
            label="偏好名称"
            htmlFor="rtl-review-input"
            rules={[{ required: true, whitespace: true, message: '请输入偏好名称。' }]}
          >
            <Input id="rtl-review-input" />
          </Form.Item>
          <Button id="rtl-review-save" type="primary" htmlType="submit">
            保存
          </Button>
        </Form>
        <p id="rtl-review-value" role="status">
          已保存：{saved || '未设置'}
        </p>
        {error && <p role="alert" className="prototype-error">{error}</p>}
      </Card>
    </>
  );
}
